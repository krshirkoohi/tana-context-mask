import re
import math
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Tuple, Any
from ..models.node import TanaNode, NodeTag, NodeField

MONTH_NAMES = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12
}

DATE_FIELD_NAMES = {
    "date", "due date", "event date", "when", "occurrence date",
    "original date", "scheduled date", "start date", "target date"
}

def parse_iso_date(text: str) -> Optional[str]:
    """Extracts YYYY-MM-DD from a text string if present."""
    if not text:
        return None
    # 1. Matches YYYY-MM-DD
    m = re.search(r'\b(19\d\d|20\d\d)-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b', text)
    if m:
        return m.group(0)
    
    # 2. Matches "1 September 2019", "September 1, 2019", "Sep 1 2019", "1st Sep 2019"
    months_pattern = r'(?:' + '|'.join(MONTH_NAMES.keys()) + r')'
    m2 = re.search(rf'\b({months_pattern})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,)?\s+(19\d\d|20\d\d)\b', text, re.IGNORECASE)
    if m2:
        m_str, d_str, y_str = m2.group(1).lower(), m2.group(2), m2.group(3)
        month = MONTH_NAMES.get(m_str, 1)
        day = int(d_str)
        return f"{int(y_str):04d}-{month:02d}-{day:02d}"

    m3 = re.search(rf'\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({months_pattern})\s+(19\d\d|20\d\d)\b', text, re.IGNORECASE)
    if m3:
        d_str, m_str, y_str = m3.group(1), m3.group(2).lower(), m3.group(3)
        month = MONTH_NAMES.get(m_str, 1)
        day = int(d_str)
        return f"{int(y_str):04d}-{month:02d}-{day:02d}"

    return None

def is_day_node(node: TanaNode) -> Tuple[bool, Optional[str]]:
    """Checks if node is a Day calendar node and extracts its date string."""
    # Tag check (e.g. tag_name "Day" or tag_id "1Kcq0q_pf5Fn")
    tag_names = [t.tag_name.lower() for t in node.supertags]
    tag_ids = [t.tag_id for t in node.supertags]
    has_day_tag = ("day" in tag_names or "1Kcq0q_pf5Fn" in tag_ids)

    # Clean name
    clean_name = re.sub(r'<[^>]+>', '', node.name).strip()
    parsed_date = parse_iso_date(clean_name)

    if parsed_date:
        return True, parsed_date
    if has_day_tag:
        # If tagged Day but name didn't directly match regex, check created_at or node fields
        field_date = parse_field_date(node)
        if field_date:
            return True, field_date
        if node.created_at:
            c_date = parse_iso_date(node.created_at)
            if c_date:
                return True, c_date
    return False, None

def is_month_node(node: TanaNode) -> Tuple[bool, Optional[str]]:
    """Checks if node represents a Month calendar node (e.g. 'September 2019' or '2019-09')."""
    clean_name = re.sub(r'<[^>]+>', '', node.name).strip()
    months_pattern = r'(?:' + '|'.join(MONTH_NAMES.keys()) + r')'
    
    m = re.search(rf'\b({months_pattern})\s+(19\d\d|20\d\d)\b', clean_name, re.IGNORECASE)
    if m:
        m_str, y_str = m.group(1).lower(), m.group(2)
        month = MONTH_NAMES.get(m_str, 1)
        return True, f"{int(y_str):04d}-{month:02d}"

    m2 = re.search(r'\b(19\d\d|20\d\d)-(0[1-9]|1[0-2])\b', clean_name)
    if m2:
        return True, f"{m2.group(1)}-{m2.group(2)}"

    tag_names = [t.tag_name.lower() for t in node.supertags]
    if "month" in tag_names:
        return True, None
    return False, None

def is_year_node(node: TanaNode) -> Tuple[bool, Optional[str]]:
    """Checks if node represents a Year calendar node (e.g. '2019')."""
    clean_name = re.sub(r'<[^>]+>', '', node.name).strip()
    m = re.fullmatch(r'(19\d\d|20\d\d)(?:\s+Year)?', clean_name, re.IGNORECASE)
    if m:
        return True, m.group(1)
    tag_names = [t.tag_name.lower() for t in node.supertags]
    if "year" in tag_names:
        return True, None
    return False, None

def parse_field_date(node: TanaNode) -> Optional[str]:
    """Finds explicit date field on the node."""
    for field in node.fields:
        f_name = field.field_name.lower().strip()
        if f_name in DATE_FIELD_NAMES or "date" in f_name or "when" in f_name:
            val = (field.value_text or "").strip()
            parsed = parse_iso_date(val)
            if parsed:
                return parsed
    return None

def resolve_node_provenance(node: TanaNode, parent_chain: List[TanaNode]) -> Dict[str, Any]:
    """
    Derives effective_date and calendar lineage for a node according to precedence:
    1. Nearest ancestor Day/calendar node
    2. Explicit date field on node
    3. Other explicit calendar relationship
    4. Created timestamp (weak fallback)
    """
    # Check if the node itself is a Day node
    self_is_day, self_day_date = is_day_node(node)
    if self_is_day and self_day_date:
        return {
            "effective_date": self_day_date,
            "date_source": "day_node",
            "date_source_node_id": node.id,
            "calendar_distance": 0,
            "calendar_path": self_day_date,
            "ancestor_day_node_id": node.id,
            "ancestor_week_node_id": None,
            "ancestor_month_node_id": None,
            "ancestor_year_node_id": self_day_date[:4]
        }

    # 1. Search parent chain for nearest Day/calendar ancestor
    ancestor_day_id = None
    ancestor_day_date = None
    ancestor_month_id = None
    ancestor_year_id = None
    calendar_distance = None

    for dist, p in enumerate(parent_chain, start=1):
        p_is_day, p_day_date = is_day_node(p)
        if p_is_day and p_day_date:
            if ancestor_day_id is None:
                ancestor_day_id = p.id
                ancestor_day_date = p_day_date
                calendar_distance = dist

        p_is_month, _ = is_month_node(p)
        if p_is_month and ancestor_month_id is None:
            ancestor_month_id = p.id

        p_is_year, p_year = is_year_node(p)
        if p_is_year and ancestor_year_id is None:
            ancestor_year_id = p.id

    explicit_field_date = parse_field_date(node)

    # 1. Ancestor Day node has highest precedence for historical grounding
    if ancestor_day_date:
        cal_path = ancestor_day_date
        year_str = ancestor_day_date[:4]
        return {
            "effective_date": ancestor_day_date,
            "date_source": "day_node",
            "date_source_node_id": ancestor_day_id,
            "calendar_distance": calendar_distance,
            "calendar_path": cal_path,
            "ancestor_day_node_id": ancestor_day_id,
            "ancestor_week_node_id": None,
            "ancestor_month_node_id": ancestor_month_id,
            "ancestor_year_node_id": ancestor_year_id or year_str
        }

    # 2. Explicit date field
    if explicit_field_date:
        return {
            "effective_date": explicit_field_date,
            "date_source": "field",
            "date_source_node_id": node.id,
            "calendar_distance": 0,
            "calendar_path": explicit_field_date,
            "ancestor_day_node_id": None,
            "ancestor_week_node_id": None,
            "ancestor_month_node_id": ancestor_month_id,
            "ancestor_year_node_id": explicit_field_date[:4]
        }

    # 3. Created timestamp fallback (weak)
    if node.created_at:
        c_date = parse_iso_date(node.created_at)
        if c_date:
            return {
                "effective_date": c_date,
                "date_source": "fallback_created",
                "date_source_node_id": node.id,
                "calendar_distance": None,
                "calendar_path": c_date,
                "ancestor_day_node_id": None,
                "ancestor_week_node_id": None,
                "ancestor_month_node_id": None,
                "ancestor_year_node_id": c_date[:4]
            }

    # 4. Undated
    return {
        "effective_date": None,
        "date_source": "none",
        "date_source_node_id": None,
        "calendar_distance": None,
        "calendar_path": None,
        "ancestor_day_node_id": None,
        "ancestor_week_node_id": None,
        "ancestor_month_node_id": None,
        "ancestor_year_node_id": None
    }

def compute_temporal_score(
    node_date_str: Optional[str],
    target_date_str: Optional[str],
    date_from_str: Optional[str] = None,
    date_to_str: Optional[str] = None
) -> float:
    """
    Computes temporal proximity score in [0.0, 1.0].
    0 days = 1.00, 1 day = 0.95, 3 days = 0.85, 7 days = 0.70, 14 days = 0.50, 30 days = 0.25, >30 days = decay
    """
    if not node_date_str:
        return 0.0

    try:
        n_date = datetime.strptime(node_date_str[:10], "%Y-%m-%d").date()
    except ValueError:
        return 0.0

    # Range filter bounds check
    if date_from_str:
        try:
            d_from = datetime.strptime(date_from_str[:10], "%Y-%m-%d").date()
            if n_date < d_from:
                return 0.0
        except ValueError:
            pass

    if date_to_str:
        try:
            d_to = datetime.strptime(date_to_str[:10], "%Y-%m-%d").date()
            if n_date > d_to:
                return 0.0
        except ValueError:
            pass

    if not target_date_str:
        return 1.0  # Inside range without specific target

    try:
        t_date = datetime.strptime(target_date_str[:10], "%Y-%m-%d").date()
    except ValueError:
        return 0.5

    delta_days = abs((n_date - t_date).days)

    # Standard piecewise decay schedule
    if delta_days == 0:
        return 1.00
    elif delta_days == 1:
        return 0.95
    elif delta_days <= 3:
        return 0.85
    elif delta_days <= 7:
        return 0.70
    elif delta_days <= 14:
        return 0.50
    elif delta_days <= 30:
        return 0.25
    else:
        # Exponential tail decay
        return max(0.0, 0.25 * math.exp(-(delta_days - 30) / 60.0))

def classify_temporal_relationship(
    node: TanaNode,
    target_date_str: Optional[str] = None,
    target_year: Optional[str] = None
) -> str:
    """
    Classifies the node's temporal relationship into:
    - 'contemporaneous'
    - 'retrospective'
    - 'undated'
    - 'conflicting'
    """
    field_date = parse_field_date(node)
    
    # Check for conflict: explicit field date year != Day node year
    if field_date and node.effective_date and node.date_source == "day_node":
        if field_date[:4] != node.effective_date[:4]:
            return "conflicting"

    # Extract any year mentioned in the plain text
    full_text = f"{node.name} {node.description or ''}".lower()
    text_years = re.findall(r'\b(19\d\d|20\d\d)\b', full_text)

    # Target reference year
    ref_year = target_year or (target_date_str[:4] if target_date_str else None)

    # If the node is under a Day node of a different year (e.g. 2026) but talks about the target year (e.g. 2019)
    if node.effective_date and ref_year:
        node_year = node.effective_date[:4]
        if node_year != ref_year and ref_year in text_years:
            return "retrospective"

        if node_year == ref_year:
            if node.date_source in ["day_node", "field", "explicit_rel"]:
                return "contemporaneous"
            elif node.date_source == "fallback_created":
                return "contemporaneous"

    # If node has no valid date provenance but mentions the target year
    if not node.effective_date or node.date_source == "none":
        return "undated"

    return "contemporaneous"

def parse_temporal_intent(
    query_text: str,
    reference_date: Optional[date] = None
) -> Dict[str, Any]:
    """
    Detects historical / autobiographical intent and extracts target date/ranges.
    Examples:
    - "What was I doing this time in 2019?" (on 2026-09-01 -> target_date: 2019-09-01)
    - "What was happening around September 2018?" -> target_date: 2018-09-15, date_from: 2018-09-01, date_to: 2018-09-30
    - "What was I working on during 2019?" -> target_date: 2019-07-01, date_from: 2019-01-01, date_to: 2019-12-31
    - "What was going on around this date last year?" -> target_date: <last year same date>
    """
    ref = reference_date or date.today()
    q = query_text.strip()
    q_lower = q.lower()

    temporal_indicators = [
        r'this time in (\d{4})',
        r'around (?:the\s+)?(\d{1,2}(?:st|nd|rd|th)?\s+)?([a-z]+)\s+(\d{4})',
        r'in ([a-z]+)\s+(\d{4})',
        r'during (\d{4})',
        r'back in (\d{4})',
        r'in (\d{4})',
        r'last year',
        r'(\d+)\s+years ago',
        r'around this date last year',
        r'what was i doing',
        r'what was happening',
        r'when was i',
        r'at that time'
    ]

    has_intent = False
    for pat in temporal_indicators:
        if re.search(pat, q_lower):
            has_intent = True
            break

    target_date = None
    date_from = None
    date_to = None
    temporal_mode = "none"

    if has_intent:
        temporal_mode = "strict"

        # 1. "this time in YYYY" / "around this time in YYYY"
        m_this_time = re.search(r'(?:around\s+)?this time (?:in|of)\s+(\d{4})', q_lower)
        if m_this_time:
            yr = int(m_this_time.group(1))
            try:
                t_d = date(yr, ref.month, ref.day)
            except ValueError:
                t_d = date(yr, ref.month, 28)
            target_date = t_d.isoformat()
            date_from = (t_d - timedelta(days=14)).isoformat()
            date_to = (t_d + timedelta(days=14)).isoformat()

        # 2. "around [Month] [YYYY]" or "in [Month] [YYYY]"
        months_pattern = r'(?:' + '|'.join(MONTH_NAMES.keys()) + r')'
        m_month_yr = re.search(rf'(?:around|in|during)\s+({months_pattern})\s+(\d{{4}})', q_lower)
        if not target_date and m_month_yr:
            m_str, y_str = m_month_yr.group(1).lower(), m_month_yr.group(2)
            month = MONTH_NAMES.get(m_str, 1)
            yr = int(y_str)
            target_date = f"{yr:04d}-{month:02d}-15"
            date_from = f"{yr:04d}-{month:02d}-01"
            # End of month
            if month in [1, 3, 5, 7, 8, 10, 12]:
                last_day = 31
            elif month in [4, 6, 9, 11]:
                last_day = 30
            else:
                last_day = 29 if (yr % 4 == 0 and (yr % 100 != 0 or yr % 400 == 0)) else 28
            date_to = f"{yr:04d}-{month:02d}-{last_day:02d}"

        # 3. "during YYYY" or "in YYYY" or "back in YYYY"
        m_yr = re.search(r'(?:during|in|back in|around)\s+(\d{4})', q_lower)
        if not target_date and m_yr:
            yr = int(m_yr.group(1))
            target_date = f"{yr:04d}-07-01"
            date_from = f"{yr:04d}-01-01"
            date_to = f"{yr:04d}-12-31"

        # 4. "last year" or "around this date last year"
        if not target_date and ("last year" in q_lower or "around this date last year" in q_lower):
            yr = ref.year - 1
            try:
                t_d = date(yr, ref.month, ref.day)
            except ValueError:
                t_d = date(yr, ref.month, 28)
            target_date = t_d.isoformat()
            date_from = (t_d - timedelta(days=14)).isoformat()
            date_to = (t_d + timedelta(days=14)).isoformat()

        # 5. "X years ago"
        m_ago = re.search(r'(\d+)\s+years ago', q_lower)
        if not target_date and m_ago:
            yr_diff = int(m_ago.group(1))
            yr = ref.year - yr_diff
            try:
                t_d = date(yr, ref.month, ref.day)
            except ValueError:
                t_d = date(yr, ref.month, 28)
            target_date = t_d.isoformat()
            date_from = (t_d - timedelta(days=14)).isoformat()
            date_to = (t_d + timedelta(days=14)).isoformat()

    return {
        "has_temporal_intent": has_intent,
        "target_date": target_date,
        "date_from": date_from,
        "date_to": date_to,
        "temporal_mode": temporal_mode,
        "cleaned_query": q
    }
