import streamlit as st
import datetime

# --- CONFIGURATION & VEHICLE CONSTANTS ---
MPG = 16.0
DEFAULT_GAS_PRICE = 4.30

# --- CORE LOGIC 1: FINANCIAL GRADER ---
def calculate_metrics(gross, hours, miles, gas_price):
    if hours <= 0 or miles <= 0:
        return None
    
    hourly_rate = gross / hours
    mile_rate = gross / miles
    fuel_cost = (miles / MPG) * gas_price
    net_profit = gross - fuel_cost
    
    # Grading Scale Based on Dollar-per-Mile Efficiency
    if mile_rate >= 2.50:
        grade = "A (Highly Efficient)"
    elif mile_rate >= 1.50:
        grade = "B (Moderate Efficiency)"
    else:
        grade = "C (High Burn / Low Return)"
        
    return {
        "hourly_rate": round(hourly_rate, 2),
        "mile_rate": round(mile_rate, 2),
        "fuel_cost": round(fuel_cost, 2),
        "net_profit": round(net_profit, 2),
        "grade": grade
    }

# --- CORE LOGIC 2: EVENTS ENGINE ---
def generate_schedule(events, is_weekend=True):
    # Base schedule template
    timeline = []
    
    # Check for major football games first
    football_game = next((e for e in events if e['tier'] == "CRITICAL IMPACT"), None)
    
    if football_game:
        kickoff = football_game['time']
        timeline.append({"time": "11:30 AM - 2:30 PM", "app": "UberXL / Lyft XL ONLY", "action": "Stage at East AA / Ypsi hotel corridors. Avoid downtown gates."})
        timeline.append({"time": "2:30 PM - 5:30 PM", "app": "OFFLINE", "action": f"Take your break at home base (Arbor Circle). Avoid kickoff traffic entirely."})
        timeline.append({"time": "5:30 PM - 7:00 PM", "app": "DoorDash / Uber Eats", "action": "Run tight dinner stacks along Carpenter Road. Avoid stadium core."})
        timeline.append({"time": "7:00 PM - 10:30 PM+", "app": "UberXL / Lyft XL ONLY", "action": f"Post-game release! Camp at Industrial Hwy periphery (CVS/Produce Station). Send walk-out texts."})
    else:
        # Standard Non-Game Day Strategy (Evening Focused)
        timeline.append({"time": "5:00 PM - 8:00 PM", "app": "DoorDash / Uber Eats", "action": "Run high-dollar dinner stacks from Carpenter Rd / Washtenaw corridor."})
        
        # Check if an indoor high-density or theater event lets out during nightlife
        indoor_event = next((e for e in events if e['tier'] in ["HIGH DENSITY INDOOR", "URBAN HUB DOWNTOWN"]), None)
        if indoor_event:
            release_time = indoor_event['time']
            timeline.append({"time": "8:00 PM - 11:00 PM+", "app": "UberXL / Uber Comfort ON", "action": f"Targeting {indoor_event['name']} release at {release_time}. Stage at {indoor_event['venue']} perimeter."})
        else:
            timeline.append({"time": "8:00 PM - 10:30 PM+", "app": "UberXL / Uber Comfort ON", "action": "Standard lounge and bar surges. Keep standard UberX OFF."})
            
    return timeline

# --- STREAMLIT UI LAYOUT ---
st.set_page_config(page_title="Ann Arbor Gig Pilot", layout="centered", initial_sidebar_state="collapsed")

st.title("🛞 Ann Arbor & Ypsi Gig Pilot")
st.caption("Tailored for Kia Telluride XL (16 MPG) | Base Camp: Arbor Circle East")

tab1, tab2 = st.tabs(["🗓️ Dynamic Schedule", "📊 Earnings Grader"])

with tab1:
    st.header("This Week's Strategy Matrix")
    
    # Mocking sample raw events data for the pipeline
    current_events = [
        {"name": "Michigan vs. Penn State Football", "venue": "Michigan Stadium", "time": "3:30 PM", "tier": "CRITICAL IMPACT"},
        {"name": "U of M Basketball Game", "venue": "Crisler Center", "time": "7:00 PM", "tier": "HIGH DENSITY INDOOR"},
        {"name": "Concert Event", "venue": "Hill Auditorium", "time": "8:00 PM", "tier": "URBAN HUB DOWNTOWN"}
    ]
    
    # Filter settings for user
    active_tier = st.selectbox("Simulate Event Footprint:", ["Football Game Day (3:30 PM)", "Standard Non-Game Weekend (With Concert/Basketball)"])
    
    if "Football" in active_tier:
        selected_events = [current_events[0]]
    else:
        selected_events = [current_events[1], current_events[2]]
        
    st.subheader("Upcoming High-Yield Local Events")
    for ev in selected_events:
        st.info(f"**{ev['name']}** at {ev['venue']} ({ev['time']}) - Tier: *{ev['tier']}*")
        
    st.subheader("Your Targeted Shift Roadmap")
    optimized_schedule = generate_schedule(selected_events)
    
    for slot in optimized_schedule:
        with st.container():
            st.markdown(f"#### ⏱️ {slot['time']}")
            st.markdown(f"**App Stack:** `{slot['app']}`")
            st.markdown(f"**Action Plan:** {slot['action']}")
            st.divider()

with tab2:
    st.header("Weekly Scorecard Grader")
    
    with st.form("grader_form"):
        gross = st.number_input("Gross Earnings (\$)", min_value=0.0, value=340.90)
        hours = st.number_input("Total Online Hours", min_value=0.0, value=10.1)
        miles = st.number_input("Total Driven Miles (Including Deadheads)", min_value=0.0, value=98.6)
        gas_price = st.number_input("Current Gas Price (\$/Gal)", min_value=0.0, value=DEFAULT_GAS_PRICE)
        submitted = st.form_submit_button("Calculate My Grade")
        
    if submitted:
        metrics = calculate_metrics(gross, hours, miles, gas_price)
        if metrics:
            st.success(f"### Overall Efficiency Grade: {metrics['grade']}")
            
            col1, col2 = st.columns(2)
            col1.metric("Earnings Per Hour", f"\${metrics['hourly_rate']}/hr")
            col2.metric("Earnings Per Mile", f"\${metrics['mile_rate']}/mi")
            
            col3, col4 = st.columns(2)
            col3.metric("Estimated Fuel Cost", f"\${metrics['fuel_cost']}")
            col4.metric("True Take-Home Net Profit", f"\${metrics['net_profit']}")
