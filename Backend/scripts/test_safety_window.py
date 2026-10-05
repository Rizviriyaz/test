"""
Verify safety events scaling across 24h vs 7d (168h).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.fleet_analytics import get_safety_analytics

print("Fetching Safety Analytics for 24h (bypassing cache)...")
s24 = get_safety_analytics(hours=24)
print(f"24h Summary: Total Events = {s24['summary']['total_events']}, Braking = {s24['summary']['harsh_braking']}, Accel = {s24['summary']['harsh_acceleration']}, Cornering = {s24['summary']['harsh_cornering']}, Vehicles Affected = {s24['summary']['vehicles_affected']}")

print("\nFetching Safety Analytics for 7d (168h)...")
s7d = get_safety_analytics(hours=168)
print(f"7d Summary:  Total Events = {s7d['summary']['total_events']}, Braking = {s7d['summary']['harsh_braking']}, Accel = {s7d['summary']['harsh_acceleration']}, Cornering = {s7d['summary']['harsh_cornering']}, Vehicles Affected = {s7d['summary']['vehicles_affected']}")

print(f"\nVerification Check: 7d ({s7d['summary']['total_events']}) >= 24h ({s24['summary']['total_events']}): {s7d['summary']['total_events'] >= s24['summary']['total_events']}")
