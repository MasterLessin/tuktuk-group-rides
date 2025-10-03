# utils.py
def format_rides(rides, page, total, page_size, role):
    if not rides:
        return "No rides found."

    lines = [f"Ride history for {role} (page {page+1})"]
    for ride in rides:
        lines.append(
            f"Ride #{ride['id']} | {ride['start_location']} → {ride['end_location']} | "
            f"Status: {ride['status']} | Driver: {ride.get('driver_id', 'N/A')}"
        )
    lines.append(f"Total rides: {total}")
    return "\n".join(lines)
