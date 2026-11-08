"""The policy wording and the claims manual, split into their numbered sections."""

from typing import Any

DOCUMENTS = {"policy_wording": "Policy wording", "claims_manual": "Claims manual"}
LINE_LENGTH = 200


def split(text: str) -> tuple[str, list[dict[str, str]]]:
    """Split a Markdown document into its title and its `## ` sections."""
    title = ""
    sections: list[dict[str, str]] = []
    for line in text.splitlines():
        if line.startswith("# ") and not title:
            title = line[2:].strip()
        elif line.startswith("## "):
            number, _, name = line[3:].partition(". ")
            sections.append({"section": number, "title": name.strip(), "text": ""})
        elif sections:
            sections[-1]["text"] += line + "\n"
    for section in sections:
        section["text"] = section["text"].strip()
    return title, sections


def contents(text: str) -> dict[str, Any]:
    """The document's title and its sections' numbers and titles."""
    title, sections = split(text)
    return {
        "title": title,
        "sections": [{k: s[k] for k in ["section", "title"]} for s in sections],
    }


def find(text: str, number: str) -> dict[str, str] | None:
    """One section of the document, or None if there is no such section."""
    return next((s for s in split(text)[1] if s["section"] == number), None)


def search(document: str, text: str, phrase: str) -> list[dict[str, str]]:
    """The sections that contain the phrase, each with its first matching line."""
    needle = phrase.lower()
    matches = []
    for section in split(text)[1]:
        lines = [section["title"], *section["text"].splitlines()]
        line = next((line for line in lines if needle in line.lower()), None)
        if line is not None:
            matches.append(
                {
                    "document": document,
                    "section": section["section"],
                    "title": section["title"],
                    "line": line.strip()[:LINE_LENGTH],
                }
            )
    return matches
