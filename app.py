import streamlit as st
import datetime

# --- CONFIGURATION & VEHICLE CONSTANTS ---
MPG = 16.0

# --- DATA: EXPANDED EVENT DATABASE ---
# Mocking data framework for UMich and EMU tracking
EVENTS_DATABASE = {
    datetime.date(2026, 10, 17): [
        {"name": "Michigan vs. Penn State Football", "venue": "Michigan Stadium (A2)", "time": "3:30 PM", "tier": "CRITICAL IMPACT"},
        {"name": "EMU vs. Central Michigan", "venue": "Rynearson Stadium (Ypsi)", "time": "1:00 PM", "tier": "EMU GAME DAY"}
    ],
    datetime.date(2026, 10, 24): [
        {"name": "UMich Basketball Season Opener", "venue": "Crisler Center (A2)", "time": "7:00 PM", "tier": "HIGH DENSITY INDOOR"},
        {"name": "Hill Auditorium Concert Series", "venue": "Hill Auditorium (Downtown)", "time": "8:00 PM", "tier": "URBAN HUB DOWNTOWN"}
    ],
    # Fallback default empty slate template
}

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

def generate_schedule(events, base_camp):
    timeline = []
    
    football_game = next((e for e in events if e['tier'] == "CRITICAL IMPACT"), None)
    emu_game = next((e for e in events if e['tier'] == "EMU GAME DAY"), None)
    indoor_event = next((e for e in events if e['tier'] in ["HIGH DENSITY INDOOR", "URBAN HUB DOWNTOWN"]), None)
    
    if football_game:
        timeline.append({"time": "11:30 AM - 2:30 PM", "app": "UberXL / Lyft XL ONLY", "action": f"Stage at East AA / Ypsi hotel corridors. Set destination filters toward {base_camp} area boundaries to prevent cross-town deadheads."})
        
        if emu_game:
            timeline.append({"time": "11:30 AM - 1:00 PM", "app": "TRAFFIC WARNING", "action": f"EMU Kickoff active at {emu_game['time']}. Avoid Hewitt Rd near Washtenaw completely."})
            
        timeline.append({"time": "2:30 PM - 5:30 PM", "app": "OFFLINE", "action": f"Head directly back to {base_camp}. Core gridlock active. Avoid stadium idling entirely."})
        timeline.append({"time": "5:30 PM - 7:00 PM", "app": "DoorDash / Uber Eats", "action": "Run tight dinner stacks along Carpenter Road commercial blocks. Keep deliveries moving East/North."})
        timeline.append({"time": "7:00 PM - 10:30 PM+", "app": "UberXL / Lyft XL ONLY", "action": f"Post-game release! Camp at Industrial Hwy periphery (CVS/Produce Station). Drop off passengers, exit via I-94 back to {base_camp}."})
    else:
        timeline.append({"time": "5:00 PM - 8:00 PM", "app": "DoorDash / Uber Eats", "action": "Run high-dollar dinner stacks from Carpenter Rd / Washtenaw corridor."})
        if indoor_event:
            timeline.append({"time": "8:00 PM - 11:00 PM+", "app": "UberXL / Uber Comfort ON", "action": f"Targeting {indoor_event['name']} release at {indoor_event['time']}. Stage directly at {indoor_event['venue']} perimeter escape lanes."})
        else:
            timeline.append({"time": "8:00 PM - 10:30 PM+", "app": "UberXL / Uber Comfort ON", "action": "Standard weekend bar surges. Keep standard UberX OFF."})
            
    return timeline

# --- FRONTEND THEME ---
st.set_page_config(page_title="Ann Arbor Gig Pilot", layout="centered")

st.title("🛞 Washtenaw Gig Pilot Pro")
st.markdown("---")

# --- CUSTOMIZATION SIDEBAR PANEL ---
st.sidebar.header("⚙️ Pilot Control Panel")
custom_base = st.sidebar.text_input("Set Custom Base Camp / Location:", value="2100 Arbor Circle E")
live_gas = st.sidebar.number_input("⛽ Live Gas Price Input ($/Gal):", min_value=3.00, max_value=6.00, value=4.67, step=0.01)

tab1, tab2 = st.tabs(["🗓️ Interactive Calendar & Route", "📊 Earnings Grader"])

with tab1:
    st.subheader("Select Upcoming Driving Date")
    selected_date = st.date_input("Click calendar to view schedule rules:", datetime.date(2026, 10, 17))
    
    # Grab events or generate a quiet day template
    day_events = EVENTS_DATABASE.get(selected_date, [
        {"name": "No Major Stadium Footprint", "venue": "Standard City Flow", "time": "N/A", "tier": "STANDARD"}
    ])
    
    st.markdown("### 🏟️ Tracked Local Footprint")
    for ev in day_events:
        if ev['tier'] == "CRITICAL IMPACT":
            st.error(f"🔴 **{ev['name']}** | {ev['venue']} | {ev['time']}")
        elif ev['tier'] == "EMU GAME DAY":
            st.warning(f"🟢 **{ev['name']}** | {ev['venue']} | {ev['time']}")
        else:
            st.info(f"🔵 **{ev['name']}** | {ev['venue']} | {ev['time']}")
            
    st.markdown("### ⏱️ Dynamic Shift Roadmap")
    roadmap = generate_schedule(day_events, custom_base)
    for slot in roadmap:
        with st.expander(f"🔹 {slot['time']} - {slot['app']}", expanded=True):
            st.write(slot['action'])

with tab2:
    st.subheader("Weekly Profit Calculator")
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
