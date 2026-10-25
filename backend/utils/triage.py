VALID_INCIDENT_CATEGORIES = [
    "Verbal or emotional harassment",
    "Physical ragging",
    "Hostel hazing or coercion",
    "Cyber-ragging",
    "Financial extortion",
    "Other / unsure"
]

def determine_incident_category(selected_category=None, text=""):
    """
    Determines the incident category.
    Prioritizes the student's explicit selection if valid.
    Falls back to deterministic textual heuristics if category is missing or 'Other / unsure'.
    """
    if selected_category:
        norm = selected_category.strip()
        for cat in VALID_INCIDENT_CATEGORIES:
            if norm.lower() == cat.lower():
                return cat

    text_lower = text.lower() if text else ""

    # 1. Physical ragging keywords
    if any(k in text_lower for k in [
        "hit", "beat", "slap", "punch", "assault", "physical", "push", "hurt", "injury", "bleeding", "bruise", "violence"
    ]):
        return "Physical ragging"

    # 2. Financial extortion
    if any(k in text_lower for k in [
        "money", "extort", "cash", "forced to pay", "pay for", "wallet", "demanding money", "bribe", "snatched money"
    ]):
        return "Financial extortion"

    # 3. Cyber-ragging
    if any(k in text_lower for k in [
        "whatsapp", "instagram", "social media", "cyber", "online", "troll", "meme", "posted photo", "screenshot", "group chat", "leaked"
    ]):
        return "Cyber-ragging"

    # 4. Hostel hazing or coercion
    if any(k in text_lower for k in [
        "hostel", "room", "corridor", "warden", "night", "forced to dance", "forced to undress", "kneel", "do chores", "seniors room", "locked in"
    ]):
        return "Hostel hazing or coercion"

    # 5. Verbal or emotional harassment
    if any(k in text_lower for k in [
        "verbal", "abused", "insult", "shout", "taunt", "humiliate", "mock", "threat", "slang", "vulgar words", "foul language"
    ]):
        return "Verbal or emotional harassment"

    return "Other / unsure"


def suggest_provisional_severity(category, text="", student_priority="Medium"):
    """
    Provisional severity suggestion used solely as an internal triage aid
    to help the Anti-Ragging Committee prioritize investigation timelines.
    THIS IS A TRIAGE AID ONLY, NOT A FACTUAL FINDING ON THE MERIT OF THE ALLEGATION.
    Options: 'Critical', 'High', 'Medium'.
    """
    text_lower = text.lower() if text else ""

    # Critical indicators (immediate threat, severe violence, confinement, weapons)
    critical_indicators = [
        "hospital", "bleeding", "weapon", "knife", "suicide", "kill", "locked in",
        "unconscious", "broken", "severe beating", "assaulted", "sexual", "emergency"
    ]
    if any(ind in text_lower for ind in critical_indicators):
        return "Critical"

    # High indicators (physical contact, extortion, repeated coercion)
    high_indicators = [
        "beat", "slap", "punch", "extort", "blackmail", "forced to",
        "threatened", "every day", "daily", "gang", "cornered", "strip"
    ]
    if category in ["Physical ragging", "Financial extortion"]:
        return "High"
    if any(ind in text_lower for ind in high_indicators):
        return "High"

    # Respect student's selected priority if specified as High/Critical
    if str(student_priority).strip().lower() in ["high", "critical"]:
        return "High"

    return "Medium"
