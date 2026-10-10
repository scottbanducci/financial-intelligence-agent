# Databricks notebook source

# Verify that job parameters reach the notebook.
dbutils.widgets.text("bundle_target", "")
target = dbutils.widgets.get("bundle_target")
assert target == "dev", f"Expected dev target, received: {target!r}"

# Verify that Databricks can execute a Spark calculation.
result = spark.sql("""
    SELECT COUNT(*) AS row_count, SUM(id) AS total
    FROM range(10)
""").first()

assert result["row_count"] == 10
assert result["total"] == 45

print(f"PASS: bundle target = {target}")
print("PASS: Spark processed 10 rows; sum = 45")