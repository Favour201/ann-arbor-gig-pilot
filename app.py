def generate_schedule(events, base_camp, day_of_week):
    timeline = []
    
    # Identify match conditions
    football_game = next((e for e in events if e['tier'] == "CRITICAL IMPACT"), None)
    emu_game = next((e for e in events if e['tier'] == "EMU GAME DAY"), None)
    
    # Thursday Night Focus Rule
    if day_of_week == 3:
        timeline.append({"time": "6:00 PM - 8:30 PM", "app": "DoorDash / Uber Eats", "action": f"Thursday dinner spike. Run tight restaurant loops on Carpenter Road and Washtenaw. Maintain $2.50/mi floor."})
        timeline.append({"time": "8:30 PM - 11:00 PM+", "app": "UberXL / Uber Comfort ONLY", "action": f"Thirsty Thursday surge active. Stage near student density blocks (South U / Packard) heading downtown. Keep standard UberX OFF."})
        return timeline

    # DYNAMIC GAME DAY CALCULATOR (Handles 1:00 PM, 3:30 PM, or Night Kickoffs)
    if football_game:
        # Extract the hour number from the time string (e.g., "1:00 PM" -> 13, "3:30 PM" -> 15)
        raw_time = football_game['time']
        kickoff_hour = int(raw_time.split(":")[0])
        if "PM" in raw_time and kickoff_hour != 12:
            kickoff_hour += 12
            
        # Calculate shifts dynamically based on actual kickoff hour
        pregame_start = f"{kickoff_hour - 4}:00 PM" if kickoff_hour - 4 > 12 else f"{kickoff_hour - 4}:00 AM"
        pregame_end = f"{kickoff_hour - 1}:00 PM"
        game_window = f"{kickoff_hour - 1}:00 PM - {kickoff_hour + 2}:00 PM"
        dinner_window = f"{kickoff_hour + 2}:00 PM - {kickoff_hour + 3}:30 PM"
        breakout_time = f"{kickoff_hour + 3}:30 PM"
        
        # Build dynamic shift timeline cards
        timeline.append({"time": f"{pregame_start} - {pregame_end}", "app": "UberXL / Lyft XL ONLY", "action": f"Pre-Game Surge for Kickoff at {raw_time}. Stage at East AA / Ypsi hotel corridors heading toward stadium. Avoid downtown core grids."})
        if emu_game:
            timeline.append({"time": "⚠️ DOUBLE TRAFFIC ALERT", "app": "VENUE CONFLICT", "action": f"EMU Home game overlap active: {emu_game['name']} at {emu_game['time']}. Avoid Hewitt Road corridors completely."})
        timeline.append({"time": game_window, "app": "OFFLINE", "action": f"Stadium gates closed. Head directly back to your staging base at {base_camp}. Core gridlock peak. Protect fuel cell."})
        timeline.append({"time": dinner_window, "app": "DoorDash / Uber Eats", "action": "In-game watch party dinner rush. Stacking deliveries from Carpenter Road heading East/North on wide-open roads."})
        timeline.append({"time": f"{breakout_time} - 10:30 PM+", "app": "UberXL / Lyft XL ONLY", "action": f"Post-game stadium breakout! Camp at Industrial Hwy periphery (CVS/Produce Station). Exit routes via I-94 straight back to {base_camp}."})
        return timeline
    
    # Standard Non-Game Shift (Friday/Saturday/Sunday Nights)
    else:
        if day_of_week in [4, 5]: # Friday or Saturday
            timeline.append({"time": "5:00 PM - 8:00 PM", "app": "DoorDash / Uber Eats", "action": "Weekend evening dinner rush. Maximize Carpenter Rd commercial hub stacks."})
            timeline.append({"time": "8:00 PM - 11:00 PM+", "app": "UberXL / Uber Comfort ON", "action": "Nightlife lounges and evening bar runs active. Work downtown grids safely without stadium blockades."})
        elif day_of_week == 6: # Sunday
            timeline.append({"time": "4:30 PM - 9:30 PM", "app": "100% DoorDash / Uber Eats", "action": f"Sunday recovery dinner surge. Stay parked right at {base_camp} and run high-tip restaurant deliveries cleanly."})
            
    return timeline
