"""Quick test for the new plan-column-driven due date logic."""
from backend.ai_service import _assign_due_dates_from_plan_column, _classify_plan

# ================================================
# Test Classification
# ================================================
print("=== Plan Classification Tests ===")
assert _classify_plan("Q1") == "quarterly"
assert _classify_plan("Q2") == "quarterly"
assert _classify_plan("Quarter 1") == "quarterly"
assert _classify_plan("quarter 3") == "quarterly"
assert _classify_plan("January,26") == "monthly"
assert _classify_plan("February,26") == "monthly"
assert _classify_plan("Month 1") == "monthly"
assert _classify_plan("M3") == "monthly"
assert _classify_plan("Variable Pay") == "variable"
assert _classify_plan("variable") == "variable"
assert _classify_plan("Advance") == "other"
assert _classify_plan("Placement") == "other"
print("  All classification tests passed!\n")

# ================================================
# Test 1: Quarterly — agreement date 1 Jan 2026
# ================================================
plans1 = [
    {"plan": "Advance", "amount": 10000, "due_date": "2026-01-01", "status": "Paid"},
    {"plan": "Q1", "amount": 20000, "due_date": "2026-01-01", "status": "Pending"},
    {"plan": "Q2", "amount": 20000, "due_date": "2026-04-01", "status": "Pending"},
    {"plan": "Q3", "amount": 20000, "due_date": "2026-07-01", "status": "Pending"},
    {"plan": "Q4", "amount": 20000, "due_date": "2026-10-01", "status": "Pending"},
]
result1 = _assign_due_dates_from_plan_column(plans1, "2026-01-01")
print("Test 1: Quarterly, agreement 2026-01-01")
for p in result1:
    print(f"  {p['plan']}: {p['due_date']}")
# Expected: Advance=2026-01-01 (as-is), Q1=2026-01-01 (start), Q2=Apr 1st Monday, Q3=Jul 1st Monday, Q4=Oct 1st Monday
assert result1[0]["due_date"] == "2026-01-01"  # Advance kept as-is
assert result1[1]["due_date"] == "2026-01-01"  # Q1 = start date
assert result1[2]["due_date"] == "2026-04-06"  # April first Monday
assert result1[3]["due_date"] == "2026-07-06"  # July first Monday
assert result1[4]["due_date"] == "2026-10-05"  # October first Monday
print("  PASSED!\n")

# ================================================
# Test 2: Monthly — agreement date 1 Jan 2026
# ================================================
plans2 = [
    {"plan": "January,26", "amount": 5000, "due_date": "2026-01-01", "status": "Pending"},
    {"plan": "February,26", "amount": 5000, "due_date": "2026-02-01", "status": "Pending"},
    {"plan": "March,26", "amount": 5000, "due_date": "2026-03-01", "status": "Pending"},
    {"plan": "April,26", "amount": 5000, "due_date": "2026-04-01", "status": "Pending"},
    {"plan": "May,26", "amount": 5000, "due_date": "2026-05-01", "status": "Pending"},
    {"plan": "June,26", "amount": 5000, "due_date": "2026-06-01", "status": "Pending"},
]
result2 = _assign_due_dates_from_plan_column(plans2, "2026-01-01")
print("Test 2: Monthly, agreement 2026-01-01")
for p in result2:
    print(f"  {p['plan']}: {p['due_date']}")
# Expected: Jan=2026-01-01 (start), Feb=first Monday, Mar=first Monday, etc.
assert result2[0]["due_date"] == "2026-01-01"  # First month = start date
assert result2[1]["due_date"] == "2026-02-02"  # Feb first Monday
assert result2[2]["due_date"] == "2026-03-02"  # Mar first Monday
assert result2[3]["due_date"] == "2026-04-06"  # Apr first Monday
assert result2[4]["due_date"] == "2026-05-04"  # May first Monday
assert result2[5]["due_date"] == "2026-06-01"  # Jun first Monday
print("  PASSED!\n")

# ================================================
# Test 3: Quarterly with Variable Pay
# ================================================
plans3 = [
    {"plan": "Q1", "amount": 20000, "due_date": "2026-01-01", "status": "Pending"},
    {"plan": "Q2", "amount": 20000, "due_date": "2026-04-01", "status": "Pending"},
    {"plan": "Q3", "amount": 20000, "due_date": "2026-07-01", "status": "Pending"},
    {"plan": "Q4", "amount": 20000, "due_date": "2026-10-01", "status": "Pending"},
    {"plan": "Variable Pay", "amount": 50000, "due_date": "2026-12-31", "status": "Pending"},
]
result3 = _assign_due_dates_from_plan_column(plans3, "2026-01-01")
print("Test 3: Quarterly + Variable Pay, agreement 2026-01-01")
for p in result3:
    print(f"  {p['plan']}: due_date='{p['due_date']}'")
# Expected: Q1-Q4 with proper dates, Variable Pay = empty
assert result3[0]["due_date"] == "2026-01-01"  # Q1 = start
assert result3[1]["due_date"] == "2026-04-06"  # Q2 = Apr first Monday
assert result3[2]["due_date"] == "2026-07-06"  # Q3 = Jul first Monday
assert result3[3]["due_date"] == "2026-10-05"  # Q4 = Oct first Monday
assert result3[4]["due_date"] == ""             # Variable Pay = empty
print("  PASSED!\n")

# ================================================
# Test 4: Monthly with Variable Pay
# ================================================
plans4 = [
    {"plan": "Month 1", "amount": 5000, "due_date": "2026-03-10", "status": "Pending"},
    {"plan": "Month 2", "amount": 5000, "due_date": "2026-04-01", "status": "Pending"},
    {"plan": "Month 3", "amount": 5000, "due_date": "2026-05-01", "status": "Pending"},
    {"plan": "Variable Payment", "amount": 10000, "due_date": "2026-06-01", "status": "Pending"},
]
result4 = _assign_due_dates_from_plan_column(plans4, "2026-03-10")
print("Test 4: Monthly + Variable, agreement 2026-03-10")
for p in result4:
    print(f"  {p['plan']}: due_date='{p['due_date']}'")
# Expected: Month 1=start, Month 2=Apr first Monday, Month 3=May first Monday, Variable=empty
assert result4[0]["due_date"] == "2026-03-10"  # Month 1 = start date
assert result4[1]["due_date"] == "2026-04-06"  # Month 2 = Apr first Monday
assert result4[2]["due_date"] == "2026-05-04"  # Month 3 = May first Monday
assert result4[3]["due_date"] == ""             # Variable = empty
print("  PASSED!\n")

# ================================================
# Test 5: Advance + Quarterly (advance is "other", not counted as recurring)
# ================================================
plans5 = [
    {"plan": "Advance", "amount": 10000, "due_date": "2026-01-15", "status": "Paid"},
    {"plan": "Quarter 1", "amount": 20000, "due_date": "2026-01-01", "status": "Pending"},
    {"plan": "Quarter 2", "amount": 20000, "due_date": "2026-04-01", "status": "Pending"},
    {"plan": "Quarter 3", "amount": 20000, "due_date": "2026-07-01", "status": "Pending"},
    {"plan": "Quarter 4", "amount": 20000, "due_date": "2026-10-01", "status": "Pending"},
]
result5 = _assign_due_dates_from_plan_column(plans5, "2026-01-15")
print("Test 5: Advance + Quarterly, agreement 2026-01-15 (Thursday)")
for p in result5:
    print(f"  {p['plan']}: {p['due_date']}")
# Advance kept as-is (its original due date). Quarter 1 = start date
assert result5[0]["due_date"] == "2026-01-15"  # Advance = original date as-is
assert result5[1]["due_date"] == "2026-01-15"  # Quarter 1 = agreement start
assert result5[2]["due_date"] == "2026-04-06"  # Quarter 2 = Apr first Monday
assert result5[3]["due_date"] == "2026-07-06"  # Quarter 3 = Jul first Monday
assert result5[4]["due_date"] == "2026-10-05"  # Quarter 4 = Oct first Monday
print("  PASSED!\n")

# ================================================
# Test 6: Sort with empty due dates at end
# ================================================
from backend.ai_service import _sort_plans_by_due_date

plans6 = [
    {"plan": "Variable Pay", "due_date": "", "amount": 50000},
    {"plan": "Q1", "due_date": "2026-01-01", "amount": 20000},
    {"plan": "Q3", "due_date": "2026-07-06", "amount": 20000},
    {"plan": "Q2", "due_date": "2026-04-06", "amount": 20000},
]
sorted6 = _sort_plans_by_due_date(plans6)
print("Test 6: Sort with empty due dates")
for p in sorted6:
    print(f"  {p['plan']}: due_date='{p['due_date']}'")
assert sorted6[0]["plan"] == "Q1"
assert sorted6[1]["plan"] == "Q2"
assert sorted6[2]["plan"] == "Q3"
assert sorted6[3]["plan"] == "Variable Pay"
assert sorted6[3]["due_date"] == ""
print("  PASSED!\n")

print("All tests complete!")
