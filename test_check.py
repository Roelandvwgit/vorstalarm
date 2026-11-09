import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import check

CFG = {"name": "Testhaven", "latitude": 51.67, "longitude": 4.08, "threshold": 1.0, "horizon_days": 7}
NOW = datetime(2026, 11, 9, 7, 0)


def forecast(cold=None, base=8.0, now=NOW):
    """Uurreeks van 8 dagen; cold = {uren vanaf nu: temperatuur}."""
    cold = cold or {}
    start = now.replace(hour=0)
    times, temps = [], []
    for hour in range(8 * 24):
        moment = start + timedelta(hours=hour)
        times.append(moment.isoformat(timespec="minutes"))
        temps.append(cold.get(int((moment - now).total_seconds() // 3600), base))
    return check.summarize(times, temps, now, CFG)


def active_state(now=NOW, hours=72, temp=-1.0):
    _, state = check.decide({"location": check.location_key(CFG)}, forecast({hours: temp}, now=now), CFG, now)
    return state


class Summarize(unittest.TestCase):
    def test_exact_threshold_is_not_frost(self):
        self.assertIsNone(forecast({24: 1.0})["first_frost"])

    def test_just_below_threshold_is_frost(self):
        self.assertEqual(forecast({24: 0.9})["first_frost"], NOW + timedelta(hours=24))

    def test_past_hours_are_ignored(self):
        self.assertIsNone(forecast({-5: -3.0})["first_frost"])

    def test_beyond_horizon_is_ignored(self):
        self.assertIsNone(forecast({7 * 24 + 2: -3.0})["first_frost"])

    def test_too_little_data_raises(self):
        with self.assertRaises(RuntimeError):
            check.summarize(["2026-11-09T08:00"], [3.0], NOW, CFG)

    def test_missing_values_are_skipped(self):
        times = [(NOW + timedelta(hours=h)).isoformat(timespec="minutes") for h in range(48)]
        temps = [None if h == 3 else 5.0 for h in range(48)]
        self.assertEqual(check.summarize(times, temps, NOW, CFG)["lowest"], 5.0)


class Decide(unittest.TestCase):
    def test_first_run_confirms_location(self):
        messages, state = check.decide({}, forecast(), CFG, NOW)
        self.assertEqual([m["title"] for m in messages], ["Vorstalarm actief"])
        self.assertFalse(state["active"])

    def test_new_frost_alerts_once(self):
        state = {"location": check.location_key(CFG)}
        messages, state = check.decide(state, forecast({72: -1.0}), CFG, NOW)
        self.assertEqual([m["title"] for m in messages], ["Vorst verwacht"])
        self.assertEqual(messages[0]["priority"], 1)
        messages, _ = check.decide(state, forecast({72: -1.0}), CFG, NOW + timedelta(hours=12))
        self.assertEqual(messages, [])

    def test_worse_alerts_only_from_one_degree(self):
        state = active_state()
        self.assertEqual(check.decide(state, forecast({72: -1.9}), CFG, NOW)[0], [])
        messages, _ = check.decide(state, forecast({72: -2.0}), CFG, NOW)
        self.assertEqual(messages[0]["title"], "Vorst wordt strenger")

    def test_earlier_alerts(self):
        messages, _ = check.decide(active_state(), forecast({48: -1.0}), CFG, NOW)
        self.assertEqual(messages[0]["title"], "Vorst komt eerder")

    def test_imminent_reminder_once(self):
        state = active_state()
        later = NOW + timedelta(days=2)
        messages, state = check.decide(state, forecast({24: -1.0}, now=later), CFG, later)
        self.assertEqual(messages[0]["title"], "Vorst is dichtbij")
        self.assertEqual(check.decide(state, forecast({24: -1.0}, now=later), CFG, later)[0], [])

    def test_all_clear_needs_two_checks(self):
        state = active_state()
        messages, state = check.decide(state, forecast(), CFG, NOW)
        self.assertEqual(messages, [])
        messages, state = check.decide(state, forecast(), CFG, NOW)
        self.assertEqual(messages[0]["title"], "Sein veilig")
        self.assertFalse(state["active"])

    def test_clear_counter_resets_on_frost(self):
        state = active_state()
        _, state = check.decide(state, forecast(), CFG, NOW)
        _, state = check.decide(state, forecast({72: -1.0}), CFG, NOW)
        self.assertEqual(state["clear_count"], 0)

    def test_later_second_wave_resets_baseline(self):
        state = active_state(temp=-5.0)
        messages, state = check.decide(state, forecast({120: -2.0}), CFG, NOW)
        self.assertEqual(messages, [])
        self.assertEqual(state["alerted_min"], -2.0)

    def test_location_change_alerts_for_new_place(self):
        state = active_state()
        other = dict(CFG, latitude=52.0, name="Elders")
        messages, state = check.decide(state, forecast({72: -1.0}), other, NOW)
        self.assertEqual(messages[0]["title"], "Vorst verwacht")
        self.assertIn("Elders", messages[0]["body"])

    def test_recovery_message(self):
        state = {"location": check.location_key(CFG), "failure_notified": True}
        messages, state = check.decide(state, forecast(), CFG, NOW)
        self.assertEqual(messages[0]["title"], "Vorstalarm hersteld")
        self.assertFalse(state["failure_notified"])

    def test_uncertainty_note(self):
        messages, _ = check.decide({"location": check.location_key(CFG)}, forecast({6 * 24: -1.0}), CFG, NOW)
        self.assertIn("onzeker", messages[0]["body"])
        messages, _ = check.decide({"location": check.location_key(CFG)}, forecast({72: -1.0}), CFG, NOW)
        self.assertNotIn("onzeker", messages[0]["body"])


class Other(unittest.TestCase):
    def test_heartbeat_monday_in_season(self):  # 9 nov 2026 is een maandag
        self.assertIsNotNone(check.heartbeat({}, forecast(), CFG, NOW))

    def test_heartbeat_not_twice(self):
        self.assertIsNone(check.heartbeat({"last_heartbeat": "2026-11-09"}, forecast(), CFG, NOW))

    def test_heartbeat_not_on_tuesday(self):
        later = NOW + timedelta(days=1)
        self.assertIsNone(check.heartbeat({}, forecast(now=later), CFG, later))

    def test_heartbeat_not_in_summer(self):
        summer = datetime(2026, 7, 6, 7, 0)
        self.assertIsNone(check.heartbeat({}, forecast(now=summer), CFG, summer))

    def test_exactly_one_cron_per_check_applies(self):
        tz = ZoneInfo(check.TZ_NAME)
        hours = {check.scheduled_local_hour(f"5 {h} * * *", tz) for h in (5, 6, 17, 18)}
        self.assertEqual(len(hours & check.CHECK_HOURS), 2)

    def test_config_validation(self):
        with self.assertRaises(ValueError):
            check.validate_config(dict(CFG, latitude=95))
        with self.assertRaises(KeyError):
            check.validate_config({"name": "x"})


if __name__ == "__main__":
    unittest.main()
