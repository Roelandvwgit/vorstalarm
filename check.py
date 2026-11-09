"""Vorstalarm: waarschuwt via Pushover als vorst wordt verwacht op de ingestelde locatie."""
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent
CONFIG_FILE = ROOT / "config.json"
STATE_FILE = ROOT / "state.json"
TZ_NAME = "Europe/Amsterdam"
CHECK_HOURS = {7, 19}
HEARTBEAT_MONTHS = {10, 11, 12, 1, 2, 3, 4}
WORSE_BY = 1.0  # opnieuw melden als de verwachting zoveel graden kouder wordt
CLEAR_CHECKS = 2  # aantal vorstvrije controles op rij voor "sein veilig"
UNCERTAIN_FROM_DAYS = 5  # dag 6 en later (vandaag = dag 1) is onzeker

DAYS = ["ma", "di", "wo", "do", "vr", "za", "zo"]
MONTHS = ["jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]


def fmt_temp(value):
    return f"{value:.1f}".replace(".", ",") + " °C"


def fmt_day(value):
    return f"{DAYS[value.weekday()]} {value.day} {MONTHS[value.month - 1]}"


def fmt_moment(value):
    return f"{fmt_day(value)} rond {value.hour:02d}:00"


def validate_config(cfg):
    lat, lon = float(cfg["latitude"]), float(cfg["longitude"])
    threshold, horizon = float(cfg["threshold"]), int(cfg["horizon_days"])
    if not str(cfg["name"]).strip():
        raise ValueError("locatienaam ontbreekt")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("ongeldige coördinaten")
    if not -20 <= threshold <= 15:
        raise ValueError("drempel moet tussen -20 en 15 graden liggen")
    if not 1 <= horizon <= 14:
        raise ValueError("aantal dagen moet tussen 1 en 14 liggen")
    return {"name": str(cfg["name"]).strip(), "latitude": lat, "longitude": lon,
            "threshold": threshold, "horizon_days": horizon}


def fetch_forecast(cfg):
    query = urllib.parse.urlencode({
        "latitude": cfg["latitude"], "longitude": cfg["longitude"],
        "hourly": "temperature_2m", "timezone": TZ_NAME,
        "forecast_days": cfg["horizon_days"] + 1,
    })
    url = f"https://api.open-meteo.com/v1/forecast?{query}"
    last_error = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                hourly = json.load(response)["hourly"]
            return hourly["time"], hourly["temperature_2m"]
        except Exception as error:  # netwerk- en formaatfouten worden gelijk behandeld
            last_error = error
            if attempt < 2:
                time.sleep(20)
    raise RuntimeError(f"weerbericht niet opgehaald: {last_error}")


def summarize(times, temps, now, cfg):
    """Vat de uurverwachting vanaf nu tot het einde van de horizon samen."""
    end = now + timedelta(days=cfg["horizon_days"])
    points = []
    for stamp, temp in zip(times, temps):
        moment = datetime.fromisoformat(stamp)
        if temp is not None and now - timedelta(hours=1) < moment <= end:
            points.append((moment, float(temp)))
    if len(points) < 24:
        raise RuntimeError(f"te weinig weerdata ontvangen ({len(points)} uren)")

    lowest_time, lowest = min(points, key=lambda p: p[1])
    frost = [p for p in points if p[1] < cfg["threshold"]]
    days = {}
    for moment, temp in frost:
        key = moment.date()
        days[key] = min(temp, days.get(key, temp))
    return {
        "lowest": lowest, "lowest_time": lowest_time,
        "first_frost": frost[0][0] if frost else None,
        "frost_days": sorted(days.items()),
    }


def alert_body(summary, cfg, today, intro):
    lines = [
        intro,
        f"Laagste verwachting: {fmt_temp(summary['lowest'])} op {fmt_moment(summary['lowest_time'])}.",
        f"Eerste keer onder {fmt_temp(cfg['threshold'])}: {fmt_moment(summary['first_frost'])}.",
        "",
        "Laagste per dag:",
    ]
    lines += [f"{fmt_day(day)}: {fmt_temp(temp)}" for day, temp in summary["frost_days"]]
    if any((day - today).days >= UNCERTAIN_FROM_DAYS for day, _ in summary["frost_days"]):
        lines += ["", "Let op: de verwachting vanaf dag 6 is nog onzeker."]
    return "\n".join(lines)


def message(title, body, priority=0):
    return {"title": title, "body": body, "priority": priority}


def location_key(cfg):
    return f"{cfg['latitude']:.4f},{cfg['longitude']:.4f}"


def decide(state, summary, cfg, now):
    """Bepaalt welke meldingen nodig zijn. Geeft (meldingen, nieuwe status) terug."""
    state = dict(state)
    messages = []
    today = now.date()
    name = cfg["name"]

    if state.get("failure_notified"):
        messages.append(message("Vorstalarm hersteld", f"De controle voor {name} werkt weer."))
    state["failure_notified"] = False

    if state.get("location") != location_key(cfg):
        state.update(location=location_key(cfg), active=False, clear_count=0)
        if not summary["first_frost"]:
            messages.append(message(
                "Vorstalarm actief",
                f"Bewaakt nu {name}. Geen vorst verwacht de komende {cfg['horizon_days']} dagen. "
                f"Laagste verwachting: {fmt_temp(summary['lowest'])} op {fmt_moment(summary['lowest_time'])}.",
            ))

    first = summary["first_frost"]
    if first:
        first_date = first.date()
        imminent = (first_date - today).days <= 1
        previous_first = (date_from(state.get("alerted_first")) if state.get("active") else None)
        state["clear_count"] = 0

        if previous_first and first_date > previous_first:
            # De eerder gemelde vorst is voorbij of schuift op: nieuw uitgangspunt.
            state.update(alerted_first=first_date.isoformat(), alerted_min=summary["lowest"],
                         imminent_sent=False)
            previous_first = first_date

        title, intro, send = None, None, False
        if not state.get("active"):
            title, intro, send = "Vorst verwacht", f"Er wordt vorst verwacht in {name}.", True
        elif first_date < previous_first:
            title, intro, send = "Vorst komt eerder", f"De vorst in {name} komt eerder dan gemeld.", True
        elif state["alerted_min"] - summary["lowest"] >= WORSE_BY:
            title, intro, send = "Vorst wordt strenger", f"De verwachting voor {name} is kouder geworden.", True
        elif imminent and not state.get("imminent_sent"):
            title, intro, send = "Vorst is dichtbij", f"De vorst in {name} begint vandaag of morgen.", True

        if send:
            messages.append(message(title, alert_body(summary, cfg, today, intro), priority=1))
            state.update(active=True, alerted_first=first_date.isoformat(), alerted_min=summary["lowest"],
                         imminent_sent=imminent)
    elif state.get("active"):
        state["clear_count"] = state.get("clear_count", 0) + 1
        if state["clear_count"] >= CLEAR_CHECKS:
            messages.append(message(
                "Sein veilig",
                f"Geen vorst meer verwacht in {name} de komende {cfg['horizon_days']} dagen. "
                f"Laagste verwachting: {fmt_temp(summary['lowest'])}.",
            ))
            state.update(active=False, clear_count=0, imminent_sent=False)

    state.update(last_check=now.isoformat(timespec="minutes"), last_lowest=summary["lowest"],
                 last_lowest_time=summary["lowest_time"].isoformat(timespec="minutes"))
    return messages, state


def date_from(text):
    return datetime.fromisoformat(text).date() if text else None


def heartbeat(state, summary, cfg, now):
    """Wekelijks teken van leven: maandagochtend, oktober tot en met april."""
    today = now.date().isoformat()
    if now.weekday() != 0 or now.month not in HEARTBEAT_MONTHS or state.get("last_heartbeat") == today:
        return None
    return message(
        "Vorstalarm werkt",
        f"Bewaakt {cfg['name']}. Laagste verwachting komende {cfg['horizon_days']} dagen: "
        f"{fmt_temp(summary['lowest'])} op {fmt_moment(summary['lowest_time'])}.",
    )


def scheduled_local_hour(cron, tz):
    """Lokaal uur waarvoor deze cron-regel bedoeld is; onafhankelijk van vertraging bij GitHub."""
    minute, hour = cron.split()[:2]
    moment = datetime.now(timezone.utc).replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
    return moment.astimezone(tz).hour


def send(msg):
    token, user = os.environ.get("PUSHOVER_TOKEN"), os.environ.get("PUSHOVER_USER")
    if not token or not user:
        raise RuntimeError("PUSHOVER_TOKEN of PUSHOVER_USER ontbreekt in de GitHub-secrets")
    data = urllib.parse.urlencode({
        "token": token, "user": user, "title": msg["title"],
        "message": msg["body"], "priority": msg["priority"],
    }).encode()
    with urllib.request.urlopen("https://api.pushover.net/1/messages.json", data=data, timeout=30) as response:
        response.read()


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main():
    tz = ZoneInfo(TZ_NAME)
    now = datetime.now(tz).replace(tzinfo=None, second=0, microsecond=0)
    cron = os.environ.get("SCHEDULE", "").strip()
    local_hour = scheduled_local_hour(cron, tz) if cron else None

    if cron and local_hour not in CHECK_HOURS:
        print("Deze planning hoort bij de andere tijdzone-periode (zomer/winter); overgeslagen.")
        return 0

    if os.environ.get("TEST", "").lower() == "true":
        send(message("Vorstalarm test", "De verbinding werkt. Deze melding is een test."))
        print("Testmelding verstuurd.")
        return 0

    state = load_json(STATE_FILE)
    try:
        cfg = validate_config(load_json(CONFIG_FILE))
        times, temps = fetch_forecast(cfg)
        summary = summarize(times, temps, now, cfg)
    except Exception as error:
        print(f"Controle mislukt: {error}", file=sys.stderr)
        if not state.get("failure_notified"):
            send(message("Vorstalarm storing",
                         f"De vorstcontrole is mislukt, dus er is nu geen actuele bewaking. Fout: {error}", priority=1))
            state["failure_notified"] = True
            save_state(state)
        return 1

    messages, state = decide(state, summary, cfg, now)
    pulse = heartbeat(state, summary, cfg, now) if local_hour == 7 else None
    if pulse:
        messages.append(pulse)
    for msg in messages:
        send(msg)
    if pulse:
        state["last_heartbeat"] = now.date().isoformat()
    save_state(state)
    print(f"Laagste verwachting {summary['lowest']} om {summary['lowest_time']}; {len(messages)} melding(en).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
