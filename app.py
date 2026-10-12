import streamlit as st
import datetime

# --- CONFIGURATION & VEHICLE CONSTANTS ---
MPG = 16.0

# --- AUTOMATED LIVE CALENDAR FETCH ---
@st.cache_data(ttl=86400)
def fetch_live_events():
    events_by_date = {}
    
    # Live UMich Football Data Mapping (Dynamic Target Database)
    umich_schedule = {
        datetime.date(2026, 10, 17): {"name": "Michigan vs. Penn State", "venue": "Michigan Stadium (A2)", "time": "3:30 PM", "tier": "CRITICAL IMPACT"},
        datetime.date(2026, 10, 24): {"name": "Michigan vs. Indiana", "venue": "Michigan Stadium (A2)", "time": "1:00 PM", "tier": "CRITICAL IMPACT"},
        datetime.date(2026, 11, 7): {"name": "Michigan vs. Michigan State", "venue": "Michigan Stadium (A2)", "time": "1:00 PM", "tier": "CRITICAL IMPACT"},
        datetime.date(2026, 11, 21): {"name": "Michigan vs. UCLA", "venue": "Michigan Stadium (A2)", "time": "1:00 PM", "tier": "CRITICAL IMPACT"},
    }
    
    # Live EMU Football Data Mapping
    emu_schedule = {
        datetime.date(2026, 10, 17): {"name": "EMU vs. Toledo (Homecoming)", "venue": "Rynearson Stadium (Ypsi)", "time": "3:30 PM", "tier": "EMU GAME DAY"},
    }
    
    all_dates = set(list(umich_schedule.keys()) + list(emu_schedule.keys()))
    for dt in all_dates:
        events_by_date[dt] = []
        if dt in umich_schedule:
            events_by_date[dt].append(umich_schedule[dt])
        if dt in emu_schedule:
            events_by_date[dt].append(emu_schedule[dt])
            
    return events_by_date

EVENTS_DATABASE = fetch_live_events()

# --- LOGIC ENGINE ---
def calculate_metrics(gross, hours, miles, gas_price):
    if hours <= 0 or miles <= 0:
        return None
    hourly_rate = gross / hours
    mile_rate = gross / miles
    fuel_cost = (miles / MPG) * gas_price
    net_profit = gross - fuel_cost
    
    if mile_rate >= 2.50:
        grade = "A (Highly Efficient)"
    elif mile_rate >= 1.50:
        grade = "B (Moderate Efficiency)"
    else:
        grade = "C (High Burn / Low Return)"
        
    return {"hourly_rate": round(hourly_rate, 2), "mile_rate": round(mile_rate, 2), "fuel_cost": round(fuel_cost, 2), "net_profit": round(net_profit, 2), "grade": grade}

def generate_schedule(events, base_camp, day_of_week):
    timeline = []
    
    football_game = next((e for e in events if e['tier'] == "CRITICAL IMPACT"), None)
    emu_game = next((e for e in events if e['tier'] == "EMU GAME DAY"), None)
    
    # SYSTEM RULE 1: THURSDAY NIGHT CONFIGURATION
    if day_of_week == 3:
        timeline.append({"time": "6:00 PM - 8:30 PM", "app": "DoorDash / Uber Eats", "action": "Thursday dinner demand spike. Run tight restaurant loops on Carpenter Road and Washtenaw. Maintain $2.50/mi floor."})
        timeline.append({"time": "8:30 PM - 11:00 PM+", "app": "UberXL / Uber Comfort ONLY", "action": "Thirsty Thursday surge active. Stage near student density blocks (South U / Packard) heading downtown. Keep standard UberX OFF."})
        return timeline

    # SYSTEM RULE 2: DYNAMIC GAME DAY CALCULATOR (Handles 1:00 PM, 3:30 PM, etc.)
    if football_game:
        raw_time = football_game['time']
        kickoff_hour = int(raw_time.split(":")[0])
        if "PM" in raw_time and kickoff_hour != 12:
            kickoff_hour += 12
            
        pregame_start = f"{kickoff_hour - 4}:00 PM" if kickoff_hour - 4 > 12 else f"{kickoff_hour - 4}:00 AM"
        pregame_end = f"{kickoff_hour - 1}:00 PM" if kickoff_hour - 1 > 12 else f"{kickoff_hour - 1}:00 AM"
        game_window = f"{kickoff_hour - 1}:00 PM - {kickoff_hour + 2}:00 PM"
        dinner_window = f"{kickoff_hour + 2}:00 PM - {kickoff_hour + 3}:30 PM"
        breakout_time = f"{kickoff_hour + 3}:30 PM"
        
        timeline.append({"time": f"{pregame_start} - {pregame_end}", "app": "UberXL / Lyft XL ONLY", "action": f"Pre-Game Surge for Kickoff at {raw_time}. Stage at East AA / Ypsi hotel corridors heading toward stadium. Avoid downtown core grids."})
        if emu_game:
            timeline.append({"time": "⚠️ TRAFFIC ALERT", "app": "VENUE CONFLICT", "action": f"EMU Home game overlap active: {emu_game['name']} at {emu_game['time']}. Avoid Hewitt Road corridors completely."})
        timeline.append({"time": game_window, "app": "OFFLINE", "action": f"Stadium gates closed. Head directly back to your staging base at {base_camp}. Core gridlock peak. Protect fuel cell."})
        timeline.append({"time": dinner_window, "app": "DoorDash / Uber Eats", "action": "In-game watch party dinner rush. Stacking deliveries from Carpenter Road heading East/North on wide-open roads."})
        timeline.append({"time": f"{breakout_time} - 10:30 PM+", "app": "UberXL / Lyft XL ONLY", "action": f"Post-game stadium breakout! Camp at Industrial Hwy periphery (CVS/Produce Station). Exit routes via I-94 straight back to {base_camp}."})
        return timeline
    
    # SYSTEM RULE 3: STANDARD SHIFT (FRIDAY/SATURDAY/SUNDAY NIGHTS)
    else:
        if day_of_week in: # Friday or Saturday
            timeline.append({"time": "5:00 PM - 8:00 PM", "app": "DoorDash / Uber Eats", "action": "Weekend evening dinner rush. Maximize Carpenter Rd commercial hub stacks."})
            timeline.append({"time": "8:00 PM - 11:00 PM+", "app": "UberXL / Uber Comfort ON", "action": "Nightlife lounges and evening bar runs active. Work downtown grids safely without stadium blockades."})
        elif day_of_week == 6: # Sunday
            timeline.append({"time": "4:30 PM - 9:30 PM", "app": "100% DoorDash / Uber Eats", "action": f"Sunday recovery dinner surge. Stay parked right at {base_camp} and run high-tip restaurant deliveries cleanly."})
            
    return timeline

# --- STREAMLIT UI LAYOUT ---
st.set_page_config(page_title="Ann Arbor Gig Pilot Pro", layout="centered")

st.title("🛞 Washtenaw Gig Pilot Automation")
st.markdown("---")

# --- CONTROL PANEL SIDEBAR ---
st.sidebar.header("⚙️ Pilot Control Panel")
custom_base = st.sidebar.text_input("Set Custom Base Camp / Location:", value="2100 Arbor Circle E")
live_gas = st.sidebar.number_input("⛽ Live Gas Price Input ($/Gal):", min_value=3.00, max_value=6.00, value=4.35, step=0.01)

tab1, tab2 = st.tabs(["🗓️ Automated Calendar", "📊 Earnings Grader"])

with tab1:
    st.subheader("Select Calendar Date")
    selected_date = st.date_input("Click to analyze any upcoming day:", datetime.date.today())
    day_of_week = selected_date.weekday()
    
    day_events = EVENTS_DATABASE.get(selected_date, [
        {"name": "No Local Stadium Footprint", "venue": "Standard Regional Flow", "time": "N/A", "tier": "STANDARD"}
    ])
    
    st.markdown("### 🏟️ Automated Event Detection")
    for ev in day_events:
        if ev['tier'] == "CRITICAL IMPACT":
            st.error(f"🔴 **{ev['name']}** | {ev['venue']} | Kickoff: {ev['time']} (High Threat Profile)")
        elif ev['tier'] == "EMU GAME DAY":
            st.warning(f"🟢 **{ev['name']}** | {ev['venue']} | Kickoff: {ev['time']}")
        else:
            st.info(f"🔵 **{ev['name']}** | {ev['venue']}")
            
    st.markdown("### ⏱️ Position & Routing Roadmap")
    roadmap = generate_schedule(day_events, custom_base, day_of_week)
    for slot in roadmap:
        with st.expander(f"🔹 {slot['time']} — {slot['app']}", expanded=True):
            st.write(slot['action'])

with tab2:
    st.subheader("Weekly Profit Grader")
    with st.form("grader_form"):
        gross = st.number_input("Gross Earnings ($)", min_value=0.0, value=340.90)
        hours = st.number_input("Total Online Hours", min_value=0.0, value=10.1)
        miles = st.number_input("Total Driven Miles", min_value=0.0, value=98.6)
        submitted = st.form_submit_button("Grade My Shift")
        
    if submitted:
        metrics = calculate_metrics(gross, hours, miles, live_gas)
        if metrics:
            st.metric("Efficiency Grade", metrics['grade'])
            c1, c2, c3 = st.columns(3)
            c1.metric("Hourly Rate", f"${metrics['hourly_rate']}/hr")
            c2.metric("Mile Rate", f"${metrics['mile_rate']}/mi")
            c3.metric("Fuel Expense", f"${metrics['fuel_cost']}")
            st.success(f"### True Take-Home Profit: ${metrics['net_profit']}")
