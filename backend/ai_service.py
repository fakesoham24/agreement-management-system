import json
import re
from groq import Groq
from backend.config import GROQ_API_KEY, GROQ_MODEL


def get_groq_client():
    """Get Groq client instance."""
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY not configured")
    return Groq(api_key=GROQ_API_KEY)


# All fields returned by the deep analysis
ALL_FIELDS = [
    # Agreement Overview
    "company_name", "agreement_title", "contact_person",
    "agreement_date", "effective_date", "expiry_date", "priority_level",
    "auto_renewal", "currency",
    # Dates (mapped)
    "consulting_start_date", "consulting_end_date",
    # Company Information
    "email", "phone", "alternate_contact",
    # Timeline
    "active_date", "renewal_due_date",
    # Payment Structure
    "payment_plans",
    # Consulting Visit Schedule
    "consulting_visits",
    # Legal Clauses
    "nda_included", "non_solicitation", "non_compete",
    "confidentiality_clause", "data_protection_clause",
    "arbitration_clause", "jurisdiction",
    # Services
    "services",
]


def _empty_analysis():
    """Return a default empty analysis result with all fields."""
    result = {key: None for key in ALL_FIELDS}
    result["currency"] = "₹"
    result["priority_level"] = "Casual"
    result["auto_renewal"] = "No"
    result["payment_plans"] = "[]"
    result["consulting_visits"] = "[]"
    result["services"] = "[]"
    return result


def validate_agreement_text(text: str) -> dict:
    """
    Validate that extracted text looks like a valid consulting agreement.
    Returns {"valid": True/False, "error": "reason if invalid"}
    """
    if not text or len(text.strip()) < 100:
        return {
            "valid": False,
            "error": "The uploaded file does not contain enough readable text. Please upload a valid consulting agreement document with clearly visible content."
        }

    text_lower = text.lower()
    agreement_keywords = [
        "agreement", "contract", "consulting", "services",
        "terms and conditions", "party", "parties", "scope of work",
        "payment", "compensation", "effective date", "consideration",
        "obligations", "termination", "confidential", "deliverables",
        "clause", "schedule", "annexure", "fees"
    ]

    found_keywords = sum(1 for kw in agreement_keywords if kw in text_lower)

    if found_keywords < 2:
        return {
            "valid": False,
            "error": "The uploaded file does not appear to be a valid consulting agreement. The document must contain agreement terms, payment details, or party information. Please upload a proper agreement file."
        }

    return {"valid": True, "error": None}

def _extract_json_from_text(text: str) -> dict:
    """Robustly extract a JSON object from text that may contain extra content."""
    text = text.strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON block between ```json ... ``` markers
    code_block = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
    if code_block:
        try:
            return json.loads(code_block.group(1))
        except json.JSONDecodeError:
            pass

    # Try to extract the outermost { ... } block
    # Find the first { and match to the last }
    first_brace = text.find('{')
    last_brace = text.rfind('}')
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        json_str = text[first_brace:last_brace + 1]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            # Try fixing common issues: trailing commas
            cleaned = re.sub(r',\s*}', '}', json_str)
            cleaned = re.sub(r',\s*]', ']', cleaned)
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                pass

    raise json.JSONDecodeError("Could not extract valid JSON", text, 0)


def _compute_gst_tds(amount, gst_rate=18, tds_rate=10):
    """Compute GST and TDS for a given amount."""
    if amount is None or amount == 0:
        return {"gst": 0, "tds": 0, "net": 0}
    gst = round(amount * gst_rate / 100, 2)
    tds = round(amount * tds_rate / 100, 2)
    net = round(amount + gst - tds, 2)
    return {"gst": gst, "tds": tds, "net": net}



def _validate_payment_plans(plans: list) -> list:
    """Validate and normalize payment plan entries after AI extraction."""
    validated = []
    for plan in plans:
        if not isinstance(plan, dict):
            continue

        # Normalize amount — strip currency symbols, commas, whitespace
        amt = plan.get("amount", 0) or 0
        try:
            amt = float(str(amt).replace(",", "").replace("$", "").replace("₹", "").strip())
        except (ValueError, TypeError):
            amt = 0
        if amt < 0:
            amt = abs(amt)
        plan["amount"] = round(amt, 2)

        # Compute GST/TDS if missing or invalid
        calc = _compute_gst_tds(amt)
        for field in ("gst", "tds", "net"):
            val = plan.get(field)
            if val is None or val == 0 or val == "":
                plan[field] = calc[field]
            else:
                try:
                    plan[field] = round(float(str(val).replace(",", "").replace("$", "").replace("₹", "").strip()), 2)
                except (ValueError, TypeError):
                    plan[field] = calc[field]

        # Validate due_date format (YYYY-MM-DD)
        due_date = plan.get("due_date") or ""
        if due_date and isinstance(due_date, str):
            due_date = due_date.strip()
            # Try to parse and re-format to ensure YYYY-MM-DD
            import re as _re
            if not _re.match(r'^\d{4}-\d{2}-\d{2}$', due_date):
                # Try common formats
                from datetime import datetime as _dt
                for fmt in ("%d-%m-%Y", "%m-%d-%Y", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
                    try:
                        parsed = _dt.strptime(due_date, fmt)
                        due_date = parsed.strftime("%Y-%m-%d")
                        break
                    except ValueError:
                        continue
            plan["due_date"] = due_date

        # Normalize plan name
        plan_name = plan.get("plan") or ""
        plan["plan"] = str(plan_name).strip() if plan_name else "Payment"

        # Normalize status
        status = (plan.get("status") or "Pending").strip()
        if status.lower() not in ("paid", "pending"):
            status = "Pending"
        plan["status"] = status.capitalize()

        validated.append(plan)

    return validated


def _classify_plan(plan_name: str) -> str:
    """Classify a payment plan entry by its plan name.

    Returns one of: 'quarterly', 'monthly', 'variable', 'other'.
    """
    name = (plan_name or "").strip().lower()

    # Variable pay detection
    if "variable" in name:
        return "variable"

    # Quarterly detection: Q1, Q2, Q3, Q4, Quarter 1, Quarter 2, etc.
    import re as _re
    if _re.search(r'\bq[1-4]\b', name) or _re.search(r'\bquarter\s*[1-4]?\b', name):
        return "quarterly"

    # Monthly detection: month names (full or abbreviated), or "Month 1", "Month 2", etc.
    month_names = (
        "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
        "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec"
    )
    if any(m in name for m in month_names):
        return "monthly"
    if _re.search(r'\bmonth\s*\d+\b', name) or _re.search(r'\bm\d{1,2}\b', name):
        return "monthly"

    return "other"


def _assign_due_dates_from_plan_column(plans: list, agreement_date_str: str = None) -> list:
    """Assign payment due dates based on the plan column values.

    Reads the 'plan' field of each payment entry to determine the payment
    structure (quarterly, monthly, variable) and assigns due dates accordingly:
    - First recurring payment = agreement start date (kept as-is)
    - Subsequent recurring payments = first Monday of the target month
    - Variable pay = empty due date
    - Other (advance, etc.) = kept as-is
    """
    from datetime import datetime as _dt, timedelta as _td
    from dateutil.relativedelta import relativedelta

    if not plans:
        return plans

    # Determine agreement start date
    anchor_dt = None
    if agreement_date_str:
        try:
            anchor_dt = _dt.strptime(agreement_date_str, "%Y-%m-%d")
        except (ValueError, TypeError):
            pass

    # Fallback: use first plan's due_date
    if not anchor_dt:
        for p in plans:
            dd = p.get("due_date", "")
            if dd:
                try:
                    anchor_dt = _dt.strptime(dd, "%Y-%m-%d")
                    break
                except (ValueError, TypeError):
                    continue
    if not anchor_dt:
        return plans

    # Helper: find first Monday of a given month
    def first_monday_of_month(year, month):
        dt = _dt(year, month, 1)
        weekday = dt.weekday()  # 0=Monday
        if weekday == 0:
            return dt
        days_ahead = (7 - weekday) % 7
        return dt + _td(days=days_ahead)

    # Classify all plans
    classifications = [_classify_plan(p.get("plan", "")) for p in plans]

    # Determine primary payment structure
    quarterly_count = classifications.count("quarterly")
    monthly_count = classifications.count("monthly")

    if quarterly_count > 0:
        primary = "quarterly"
        step_months = 3
    elif monthly_count > 0:
        primary = "monthly"
        step_months = 1
    else:
        # No recognizable recurring pattern — leave all as-is
        return plans

    # Collect indices of recurring plans (quarterly or monthly) in order
    recurring_indices = [
        i for i, cls in enumerate(classifications) if cls == primary
    ]

    # Assign due dates to recurring plans
    for seq, idx in enumerate(recurring_indices):
        if seq == 0:
            # First recurring payment = agreement start date (kept as-is)
            plans[idx]["due_date"] = anchor_dt.strftime("%Y-%m-%d")
        else:
            # Subsequent payments: advance from anchor by seq * step_months
            target_dt = anchor_dt + relativedelta(months=seq * step_months)
            # Snap to first Monday of target month
            fm = first_monday_of_month(target_dt.year, target_dt.month)
            plans[idx]["due_date"] = fm.strftime("%Y-%m-%d")

    # Variable pay entries: set due_date to empty
    for i, cls in enumerate(classifications):
        if cls == "variable":
            plans[i]["due_date"] = ""

    # "other" entries (advance, etc.): keep their existing due date as-is

    return plans


def _sort_plans_by_due_date(plans: list) -> list:
    """Sort payment plans by due_date ascending. Plans with empty/null due dates sort to the end."""
    from datetime import datetime as _dt
    def sort_key(p):
        dd = p.get("due_date", "") or ""
        if not dd.strip():
            return _dt(9999, 12, 31)
        try:
            return _dt.strptime(dd, "%Y-%m-%d")
        except (ValueError, TypeError):
            return _dt(9999, 12, 31)
    return sorted(plans, key=sort_key)




def analyze_agreement(text: str) -> dict:
    """Analyze agreement text using Groq LLM and extract structured data."""
    client = get_groq_client()

    system_prompt = (
        "You are a precise legal document analyst for consulting agreements. Rules:\n"
        "1. Respond with ONLY a valid JSON object, no other text.\n"
        "2. All dates in YYYY-MM-DD format. Use null for missing fields.\n"
        "3. Payment amounts as numbers (no currency symbols). GST=18%, TDS=10%, Net=Amount+GST-TDS.\n"
        "4. Preserve EXACT order of payment entries as in the document. Never reorder/merge/split.\n"
        "5. Exclude D&V Business Consulting / Dharmesh Parikh info (email: dharmesh.parikh@dvconsulting.co.in, phones: 9824009829, 9709019711) from contact fields.\n"
        "6. Currency: \"₹\" for INR, \"$\" for USD. Default \"₹\".\n"
        "7. priority_level: \"Casual\" (default) or \"High\" only.\n"
        "8. auto_renewal: \"Yes\" or \"No\". Default \"No\"."
    )

    prompt = f"""Extract ALL structured data from this consulting agreement into this JSON schema:

{{
  "company_name": "client company",
  "agreement_title": "document title",
  "contact_person": "primary contact name",
  "agreement_date": "YYYY-MM-DD",
  "effective_date": "YYYY-MM-DD",
  "consulting_start_date": "YYYY-MM-DD",
  "consulting_end_date": "YYYY-MM-DD",
  "expiry_date": "YYYY-MM-DD",
  "priority_level": "Casual|High",
  "auto_renewal": "Yes|No",
  "currency": "₹|$",
  "email": "contact email",
  "phone": "contact phone",
  "alternate_contact": "alt contact",
  "active_date": "YYYY-MM-DD",
  "renewal_due_date": "YYYY-MM-DD",
  "payment_plans": [
    {{"plan":"name as in document","amount":0.00,"gst":0.00,"tds":0.00,"net":0.00,"due_date":"YYYY-MM-DD","status":"Paid|Pending"}}
  ],
  "consulting_visits": [
    {{"role":"role name","total_visits":"count or description"}}
  ],
  "nda_included": "Yes|No|Not Mentioned",
  "non_solicitation": "Yes|No|Not Mentioned",
  "non_compete": "Yes|No|Not Mentioned",
  "confidentiality_clause": "Yes|No|Not Mentioned",
  "data_protection_clause": "Yes|No|Not Mentioned",
  "arbitration_clause": "Included|Not Mentioned",
  "jurisdiction": "city/region",
  "services": [
    {{"service_name":"name","description":"brief description"}}
  ]
}}

Due date rules for payment_plans:
- Monthly payments: consecutive months (1-month gaps). Quarterly: 3-month gaps (Q1→Q2→Q3→Q4). Variable/phase: use dates from document.
- First payment uses agreement/effective date. Subsequent payments use 1st of the target month.
- Never apply quarterly gaps to monthly payments or monthly gaps to quarterly payments.

Extract consulting_visits (team/resource allocation), all legal clauses, and all services (scope of work, deliverables).

Agreement text:
{text[:15000]}"""

    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            temperature=0.05,
            max_tokens=3000
        )

        content = response.choices[0].message.content.strip()

        # Robustly extract JSON from the response
        result = _extract_json_from_text(content)

        # Build cleaned result with all expected fields
        cleaned = {}
        for key in ALL_FIELDS:
            cleaned[key] = result.get(key)

        # Default currency
        if not cleaned["currency"] or cleaned["currency"] not in ("₹", "$"):
            cleaned["currency"] = "₹"

        # Default priority_level to Casual if not valid
        if cleaned["priority_level"] not in ("Casual", "High"):
            cleaned["priority_level"] = "Casual"

        # Default auto_renewal to No if not set
        if not cleaned.get("auto_renewal") or cleaned["auto_renewal"].strip() == "":
            cleaned["auto_renewal"] = "No"

        # Handle payment_plans — ensure it's a valid JSON string
        agreement_date_for_monday = cleaned.get("effective_date") or cleaned.get("agreement_date")
        plans = cleaned.get("payment_plans")
        if isinstance(plans, list):
            plans = _validate_payment_plans(plans)
            plans = _assign_due_dates_from_plan_column(plans, agreement_date_for_monday)
            plans = _sort_plans_by_due_date(plans)
            cleaned["payment_plans"] = json.dumps(plans)
        elif isinstance(plans, str):
            try:
                parsed = json.loads(plans)
                if isinstance(parsed, list):
                    parsed = _validate_payment_plans(parsed)
                    parsed = _assign_due_dates_from_plan_column(parsed, agreement_date_for_monday)
                    parsed = _sort_plans_by_due_date(parsed)
                    cleaned["payment_plans"] = json.dumps(parsed)
                else:
                    cleaned["payment_plans"] = "[]"
            except (json.JSONDecodeError, TypeError):
                cleaned["payment_plans"] = "[]"
        else:
            cleaned["payment_plans"] = "[]"

        # Handle consulting_visits — ensure it's a valid JSON string
        visits = cleaned.get("consulting_visits")
        if isinstance(visits, list):
            cleaned["consulting_visits"] = json.dumps(visits)
        elif isinstance(visits, str):
            try:
                parsed = json.loads(visits)
                if isinstance(parsed, list):
                    cleaned["consulting_visits"] = visits
                else:
                    cleaned["consulting_visits"] = "[]"
            except (json.JSONDecodeError, TypeError):
                cleaned["consulting_visits"] = "[]"
        else:
            cleaned["consulting_visits"] = "[]"

        # Handle services — ensure it's a valid JSON string
        services = cleaned.get("services")
        if isinstance(services, list):
            cleaned["services"] = json.dumps(services)
        elif isinstance(services, str):
            try:
                parsed = json.loads(services)
                if isinstance(parsed, list):
                    cleaned["services"] = services
                else:
                    cleaned["services"] = "[]"
            except (json.JSONDecodeError, TypeError):
                cleaned["services"] = "[]"
        else:
            cleaned["services"] = "[]"

        # Map expiry_date from consulting_end_date if not set
        if not cleaned["expiry_date"] and cleaned.get("consulting_end_date"):
            cleaned["expiry_date"] = cleaned["consulting_end_date"]

        return cleaned

    except json.JSONDecodeError as e:
        print(f"[AI_SERVICE ERROR] JSONDecodeError in analyze_agreement: {e}")
        return _empty_analysis()
    except Exception as e:
        error_str = str(e).lower()
        # Detect rate limit errors and propagate them clearly
        if "rate_limit" in error_str or "429" in error_str or "rate limit" in error_str:
            print(f"[AI_SERVICE ERROR] Rate limit hit: {e}")
            raise RuntimeError(
                "AI analysis service rate limit exceeded. Please wait a few minutes and try again. "
                "If this persists, contact your administrator."
            ) from e
        import traceback
        print(f"[AI_SERVICE ERROR] Exception in analyze_agreement: {type(e).__name__}: {e}")
        traceback.print_exc()
        return _empty_analysis()


