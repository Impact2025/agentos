"""Tests voor de leesbare vertaling van Google Calendar-API-fouten.

De kale API-fout ('404 Not Found', 'invalid_grant') vertelt niet wat de fix is;
explain_error() vertaalt hem naar een uitvoerbare melding die via de
scheduler-historie in het Actiecentrum belandt.
"""
import httpx

from backend.domains.calendar import service_google as service


def _http_error(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://example.test")
    resp = httpx.Response(code, request=req)
    return httpx.HTTPStatusError(str(code), request=req, response=resp)


def test_explain_404_noemt_serviceaccount_en_fix(monkeypatch):
    monkeypatch.setattr(service, "CALENDAR_CLIENT_EMAIL", "sa@proj.iam.gserviceaccount.com")
    monkeypatch.setattr(service, "CALENDAR_CALENDAR_ID", "vincent@voorbeeld.nl")
    msg = service.explain_error(_http_error(404))
    assert "sa@proj.iam.gserviceaccount.com" in msg
    assert "vincent@voorbeeld.nl" in msg
    assert "Delen met specifieke personen" in msg


def test_explain_403_noemt_rechten(monkeypatch):
    monkeypatch.setattr(service, "CALENDAR_CLIENT_EMAIL", "sa@proj.iam.gserviceaccount.com")
    msg = service.explain_error(_http_error(403))
    assert "Wijzigingen aanbrengen" in msg


def test_explain_invalid_grant_wijst_naar_calendar_sub():
    msg = service.explain_error(
        Exception("('invalid_grant: Invalid email or User ID', {'error': 'invalid_grant'})")
    )
    assert "CALENDAR_SUB" in msg


def test_explain_onbekende_fout_blijft_zichtbaar():
    assert "boem" in service.explain_error(RuntimeError("boem"))
