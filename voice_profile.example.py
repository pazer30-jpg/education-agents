"""
voice_profile.example.py — Template for the user's writing voice.

Copy to voice_profile.py and customize. The system reads VOICE_PROFILE
from voice_profile.py and injects it into agent prompts to shape generated
content in the user's authentic voice.

This template uses placeholders — replace with your own biographical context,
style preferences, and forbidden phrases.

The real voice_profile.py is gitignored — it contains personal information.
"""

VOICE_PROFILE = """
You write content in <user's name>'s voice.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Who is writing
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
<Brief biography — 3-5 sentences. Background, current focus, perspective.>
<What makes their voice distinctive? Insider or outsider? Practitioner or theorist?>

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Characteristic writing motion
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
<How do they typically structure a piece? What's the rhythm?>
Example: "concrete moment everyone in the field recognizes → theoretical anchor → return to the moment → open question to readers"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Voice features (10 examples)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. <Feature 1 — e.g., "parenthetical self-correction in lowercase: (or maybe better than I thought)">
2. <Feature 2 — e.g., "unexpected adjective pairing: 'dangerous and precise'">
3. <Feature 3 — e.g., "power-inversion as sentence structure: 'I didn't ask them — they asked'">
4. ...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Forbidden phrases (AI tells to avoid)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- "It is important to note"
- "Interestingly"
- "In summary"
- "It can be seen that"
- <add language-specific equivalents>

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Audience
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
<Who reads this? Practitioners? Academics? Mixed?>
<What problems are they trying to solve?>
"""


# Optional: field examples that map themes → real anecdotes
FIELD_EXAMPLES = {
    # "belonging": [
    #     {"setting": "<context>", "story": "<one-sentence anecdote>", "year": 2020},
    # ],
}


# Optional: forbidden patterns regex (more aggressive than the string list above)
FORBIDDEN_PATTERNS = [
    # r"^(In summary|To conclude)\b",
    # r"\b(very|really|just) (important|interesting|nice)\b",
]


def get_voice_prompt(platform: str = "linkedin") -> str:
    """Return platform-specific voice guidance."""
    note = {
        "linkedin": "Short, hook-driven, open question at end.",
        "blog":     "Long-form, anecdote-driven, sub-questions as section headers.",
        "podcast":  "Conversational, short sentences, [pause] markers.",
    }.get(platform, "")
    return f"{VOICE_PROFILE}\n\nPlatform guidance: {note}"


def format_examples_for_prompt(themes: list[str]) -> str:
    """Return relevant field examples as injectable prompt text."""
    if not FIELD_EXAMPLES:
        return ""
    out = []
    for theme in themes:
        for ex in FIELD_EXAMPLES.get(theme, [])[:3]:
            out.append(f"- {ex.get('setting', '')}: {ex.get('story', '')}")
    return "\n".join(out)
