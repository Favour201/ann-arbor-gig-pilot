"""Constants for the brief. Public values only: no addresses, no money, no secrets."""
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")

# Public-facing label for where shifts start and end. Never a street address:
# everything this package renders can end up on a public page or push.
BASE_LABEL = "home"

USER_AGENT = "GigPilotBrief/1.0 (https://github.com/Favour201/ann-arbor-gig-pilot)"

# NWS hourly forecast points (city centers, not personal locations).
POINTS = {"Ann Arbor": (42.2808, -83.7430), "Ypsilanti": (42.2411, -83.6130)}

# Python weekday numbers: Thu=3, Fri=4, Sat=5, Sun=6.
DRIVING_WEEKDAYS = (3, 4, 5, 6)
HORIZON_DAYS = 5  # today + the next 4 days

MPG = 16.0
OFFER_TARGET_DOLLARS_PER_MILE = 3.0  # Driver preference, not a predicted rate.

TIERS = ("CRITICAL IMPACT", "YPSI GAME DAY", "INDOOR ARENA", "PERFORMING ARTS",
         "NEIGHBORHOOD / SCHOOL")
