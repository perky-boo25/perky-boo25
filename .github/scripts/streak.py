import os, json, urllib.request
from datetime import date, datetime, timedelta, timezone

USER = os.environ["GH_USER"]
TOKEN = os.environ["GH_TOKEN"]

def gql(query, variables):
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {TOKEN}",
                 "Content-Type": "application/json",
                 "User-Agent": "streak-card"},
    )
    with urllib.request.urlopen(req) as r:
        data = json.load(r)
    if "errors" in data:
        raise SystemExit(data["errors"])
    return data["data"]

Q = """
query($login:String!, $from:DateTime!, $to:DateTime!) {
  user(login:$login) {
    createdAt
    contributionsCollection(from:$from, to:$to) {
      contributionCalendar { weeks { contributionDays { date contributionCount } } }
    }
  }
}"""

def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

now = datetime.now(timezone.utc)
today = now.date()

first = gql(Q, {"login": USER, "from": iso(now - timedelta(days=300)), "to": iso(now)})
created_year = int(first["user"]["createdAt"][:4])

counts = {}
for year in range(created_year, now.year + 1):
    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    end = min(datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc), now)
    data = gql(Q, {"login": USER, "from": iso(start), "to": iso(end)})
    cal = data["user"]["contributionsCollection"]["contributionCalendar"]
    for week in cal["weeks"]:
        for day in week["contributionDays"]:
            counts[date.fromisoformat(day["date"])] = day["contributionCount"]

total = sum(counts.values())
active = [d for d, c in counts.items() if c > 0]
first_day = min(active) if active else today

# longest streak
longest, l_start, l_end = 0, None, None
run, run_start = 0, None
d = min(counts)
while d <= today:
    if counts.get(d, 0) > 0:
        if run == 0:
            run_start = d
        run += 1
        if run > longest:
            longest, l_start, l_end = run, run_start, d
    else:
        run = 0
    d += timedelta(days=1)

# current streak (today not yet counted doesn't break it)
c_end = today if counts.get(today, 0) > 0 else today - timedelta(days=1)
d, cur = c_end, 0
while counts.get(d, 0) > 0:
    cur += 1
    d -= timedelta(days=1)
c_start = d + timedelta(days=1)

def fmt(x, year=True):
    return f"{x:%b} {x.day}" + (f", {x.year}" if year else "")

total_range = f"{fmt(first_day)} - Present"
cur_range = f"{fmt(c_start, False)} - {fmt(c_end, False)}" if cur else "No active streak"
long_range = f"{fmt(l_start, False)} - {fmt(l_end)}" if longest else "-"

svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="495" height="195" viewBox="0 0 495 195" role="img" aria-label="GitHub streak stats">
  <rect width="495" height="195" rx="10" fill="#0f1020"/>
  <line x1="165" y1="40" x2="165" y2="155" stroke="#9ca3af" stroke-opacity="0.4"/>
  <line x1="330" y1="40" x2="330" y2="155" stroke="#9ca3af" stroke-opacity="0.4"/>
  <g font-family="Segoe UI, Ubuntu, sans-serif" text-anchor="middle">
    <text x="82" y="92" font-size="28" font-weight="700" fill="#a78bfa">{total}</text>
    <text x="82" y="122" font-size="14" fill="#a78bfa">Total Contributions</text>
    <text x="82" y="146" font-size="12" fill="#9ca3af">{total_range}</text>

    <circle cx="247.5" cy="80" r="38" fill="none" stroke="#a78bfa" stroke-width="5"/>
    <text x="247.5" y="90" font-size="28" font-weight="700" fill="#e5e7eb">{cur}</text>
    <text x="247.5" y="140" font-size="14" font-weight="700" fill="#f472b6">Current Streak</text>
    <text x="247.5" y="164" font-size="12" fill="#9ca3af">{cur_range}</text>

    <text x="413" y="92" font-size="28" font-weight="700" fill="#a78bfa">{longest}</text>
    <text x="413" y="122" font-size="14" fill="#a78bfa">Longest Streak</text>
    <text x="413" y="146" font-size="12" fill="#9ca3af">{long_range}</text>
  </g>
</svg>
"""

os.makedirs("assets", exist_ok=True)
with open("assets/streak.svg", "w", encoding="utf-8") as f:
    f.write(svg)
print("total", total, "current", cur, "longest", longest)
