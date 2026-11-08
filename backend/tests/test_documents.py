import pytest


def test_list_sections_of_the_wording(client):
    contents = client.get("/api/documents/policy_wording/sections").json()
    assert contents["title"] == "UnderCover Travel Insurance: Policy Wording"
    assert [s["section"] for s in contents["sections"]] == [
        str(n) for n in range(1, 13)
    ]
    assert contents["sections"][9] == {
        "section": "10",
        "title": "How we work out your payment",
    }


def test_list_sections_of_the_manual(client):
    contents = client.get("/api/documents/claims_manual/sections").json()
    assert contents["title"] == "UnderCover Claims Handling Manual"
    assert [s["section"] for s in contents["sections"]] == [
        str(n) for n in range(1, 15)
    ]
    assert contents["sections"][8]["title"] == "Repeat claims"


def test_read_one_section(client):
    section = client.get("/api/documents/claims_manual/sections/9").json()
    assert section["document"] == "claims_manual"
    assert section["name"] == "Claims manual"
    assert section["title"] == "Repeat claims"
    assert "third claim within any 12 month period" in section["text"]
    assert "Meals without receipts" not in section["text"]
    assert "flat $35" not in section["text"]


def test_search_finds_the_sections(client):
    animals = client.get("/api/search/wild animal").json()
    assert {(m["document"], m["section"]) for m in animals} == {("claims_manual", "7")}
    assert "wild animal" in animals[0]["line"].lower()
    receipts = client.get("/api/search/receipts").json()
    assert ("claims_manual", "10") in {(m["document"], m["section"]) for m in receipts}


def test_search_with_no_matches_is_empty(client):
    assert client.get("/api/search/submarine").json() == []


@pytest.mark.parametrize(
    "path",
    [
        "/api/documents/brochure/sections",
        "/api/documents/brochure/sections/1",
        "/api/documents/claims_manual/sections/99",
    ],
)
def test_unknown_documents_and_sections_are_404(client, path):
    response = client.get(path)
    assert response.status_code == 404
    assert response.json()["detail"].startswith("There is no")
