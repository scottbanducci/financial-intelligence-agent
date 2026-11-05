"""Publisher-aware analytical views; never redefine gross flows as balances."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Mapping

from financial_intelligence_agent.engine.operators import Point, Series, dec, day


PERIODS=("D","W","M","Y")


def ambs_category(raw_type: str) -> str:
    """Canonicalize documented transaction families without merging directions."""
    typ=" ".join(raw_type.casefold().replace("_"," ").replace("-"," ").split())
    if "coupon" in typ and "swap" in typ:
        return "Coupon swaps"
    if "dollar" in typ and "roll" in typ:
        return "Dollar rolls"
    if "purchase" in typ or typ in ("buy","p"):
        return "Outright purchases"
    if "sale" in typ or "sell" in typ or typ=="s":
        return "Outright sales"
    return "Other: "+raw_type.strip()


def net_purchases(
    purchases:Mapping[date,Decimal],sales:Mapping[date,Decimal],
) -> dict[date,Decimal]:
    """Net accepted par; dates with neither transaction remain absent."""
    return {d:purchases.get(d,Decimal(0))-sales.get(d,Decimal(0))
            for d in sorted(set(purchases)|set(sales))}


def _bucket(d:date,frequency:str)->date:
    if frequency=="D":return d
    if frequency=="W":return d-timedelta(days=d.weekday())  # ISO Monday
    if frequency=="M":return date(d.year,d.month,1)
    if frequency=="Y":return date(d.year,1,1)
    raise ValueError("Aggregation must be D, W, M or Y")


def aggregate_observations(
    points,frequency:str,method:str,
)->tuple[list[date],list[float]]:
    """Sum flows, average percentage rates, last observed outstanding stock.

    Missing dates are not synthesized or forward-filled.
    """
    if frequency not in PERIODS or method not in ("sum","mean","last"):
        raise ValueError("Unsupported aggregation rule")
    buckets:dict[date,list[tuple[date,Decimal]]]=defaultdict(list)
    for p in points:
        if p.value is not None:
            buckets[_bucket(p.date,frequency)].append((p.date,p.value))
    keys=sorted(buckets)
    values=[]
    for key in keys:
        data=buckets[key]
        if method=="sum":value=sum((v for _,v in data),Decimal(0))
        elif method=="mean":value=sum((v for _,v in data),Decimal(0))/len(data)
        else:value=max(data,key=lambda x:x[0])[1]
        values.append(float(value))
    return keys,values


def treasury_maturity_breakdown(rows:list[dict]|tuple[dict,...])->list[dict]:
    """Group accepted Treasury par by reported maturity range and direction."""
    grouped:dict[tuple[str,str],dict[str,Decimal]]=defaultdict(
        lambda:{"Purchases":Decimal(0),"Sales":Decimal(0)})
    for row in rows:
        start,end=row.get("maturityRangeStart"),row.get("maturityRangeEnd")
        direction=str(row.get("operationDirection","")).upper()
        amount=dec(row.get("totalParAmtAccepted"))
        if not start or not end or direction not in ("P","S") or amount is None:
            continue
        grouped[(str(start),str(end))][
            "Purchases" if direction=="P" else "Sales"]+=amount
    return [{"Maturity from":start,"Maturity through":end,
             "Purchases (accepted par)":str(values["Purchases"]),
             "Sales (accepted par)":str(values["Sales"]),
             "Net purchases (accepted par)":
                 str(values["Purchases"]-values["Sales"])}
            for (start,end),values in sorted(grouped.items())]


def repo_contract_outstanding(
    rows:list[dict]|tuple[dict,...],start:date,end:date,
)->tuple[dict[str,Series],str]:
    """Stock of *observed* contracts only; not official total outstanding.

    Includes a contract from settlement through the day BEFORE maturity.
    Contracts initiated before the downloaded historical range are unknown.
    If settlement/maturity are unavailable, do not invent a position.
    """
    values:dict[str,dict[date,Decimal]]=defaultdict(lambda:defaultdict(Decimal))
    missing=0;eligible=0
    for row in rows:
        typ=str(row.get("operationType","")).casefold().strip()
        if typ not in ("repo","reverse repo"):continue
        amount=dec(row.get("totalAmtAccepted"))
        settle,mature=day(row.get("settlementDate")),day(row.get("maturityDate"))
        if amount is None or settle is None or mature is None or mature<=settle:
            missing+=1;continue
        eligible+=1
        label="Repo observed-contract outstanding" if typ=="repo" else (
            "Reverse repo observed-contract outstanding")
        a,b=max(start,settle),min(end,mature-timedelta(days=1))
        while a<=b:
            values[label][a]+=amount
            a+=timedelta(days=1)
    if not eligible:
        return {},"Outstanding positions unavailable: the operation records do not provide complete accepted amount, settlement date and maturity date."
    if missing:
        note=f"{missing} operation(s) lack usable amount/settlement/maturity and are excluded. "
    else:note=""
    note+=("Observed-contract outstanding is a PARTIAL estimate using fetched records, "
           "not the NY Fed's official outstanding stock. Earlier unsettled contracts "
           "and unreported maturities may be absent.")
    out={}
    for label,data in values.items():
        if not data:continue
        out[label]=Series(
            "NYFED_"+("REPO_OBS" if label.startswith("Repo") else "RRP_OBS"),
            "NYFED","USD","daily",tuple(Point(d,v) for d,v in sorted(data.items())),
            "settlement_date")
    return out,note


def swap_contract_outstanding(
    rows:list[dict]|tuple[dict,...],start:date,end:date,
)->tuple[dict[str,Series],str]:
    """Observed swap-contract positions, only when lifecycle dates exist.

    Values stay in native currency and separate by counterparty. Historical
    carry-in and unreported contracts mean these are *not* official balances.
    """
    positions:dict[str,dict[date,Decimal]]=defaultdict(lambda:defaultdict(Decimal))
    metadata={}
    missing=0
    for row in rows:
        currency=str(row.get("currency","")).upper().strip()
        amount=dec(row.get("amount"))
        a,b=day(row.get("settlementDate")),day(row.get("maturityDate"))
        if not currency or amount is None or a is None or b is None or b<=a:
            missing+=1
            continue
        counterparty=next((str(row[k]).strip() for k in (
            "counterparty","centralBank","foreignCentralBank","counterpartyBank")
            if row.get(k)),"Unspecified counterparty")
        label="Observed swap outstanding · "+currency+" · "+counterparty
        metadata[label]=currency
        current,last=max(start,a),min(end,b-timedelta(days=1))
        while current<=last:
            positions[label][current]+=amount
            current+=timedelta(days=1)
    if not positions:
        return {},"Outstanding swap positions unavailable: no complete amount, settlement and maturity records in this selection."
    from hashlib import sha256
    stock={}
    for label,values in positions.items():
        stock[label]=Series("NYFED_SWAP_"+sha256(label.encode()).hexdigest()[:14],
            "NYFED",metadata[label],"daily",
            tuple(Point(d,v) for d,v in sorted(values.items())),
            "settlement_date")
    note=("PARTIAL observed-contract positions from reported swap transactions "
          "only; not official outstanding liquidity-swap balances. "
          "Native currencies and counterparties remain separate.")
    if missing:
        note+=f" {missing} transaction(s) lacked complete lifecycle fields."
    return stock,note


def swap_dimensions(rows:list[dict]|tuple[dict,...])->list[dict]:
    """Show distinct facility/currency/counterparty flows without FX conversion."""
    values:dict[tuple[str,str,str],Decimal]=defaultdict(Decimal)
    for row in rows:
        currency=str(row.get("currency","")).strip().upper()
        amount=next((dec(row[k]) for k in (
            "amount","totalAmtAccepted","totalAmount","amountAccepted","usdAmount")
            if k in row and row[k] not in (None,"")),None)
        if not currency or amount is None:continue
        facility="USD-denominated" if currency=="USD" else "Non-USD-denominated"
        counterparty=next((str(row[k]).strip() for k in (
            "counterparty","centralBank","foreignCentralBank","counterpartyBank")
            if row.get(k)), "Not reported")
        values[(facility,currency,counterparty)]+=amount
    return [{"Facility":facility,"Currency":currency,"Counterparty":counterparty,
             "Reported transaction amount (native currency)":str(amount)}
            for (facility,currency,counterparty),amount in sorted(values.items())]
