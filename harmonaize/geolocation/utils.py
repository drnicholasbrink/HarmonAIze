# geolocation/utils.py
import math
import requests
from typing import Tuple, Optional, Dict, List
from django.conf import settings
from django.core.cache import cache


def calculate_distance_km(coord1: Tuple[float, float], coord2: Tuple[float, float]) -> float:
    """
    Calculate the distance between two coordinates using the Haversine formula.
    Returns distance in kilometers.
    """
    lat1, lon1 = coord1
    lat2, lon2 = coord2
    
    # Convert latitude and longitude from degrees to radians
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    
    # Haversine formula
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    
    # Radius of earth in kilometers
    r = 6371
    
    return c * r


def calculate_coordinate_variance(coordinates: List[Tuple[float, float]]) -> float:
    """
    Calculate the variance (spread) of a list of coordinates.
    Returns a measure of how spread out the coordinates are.
    """
    if len(coordinates) <= 1:
        return 0.0
    
    # Calculate all pairwise distances
    distances = []
    for i in range(len(coordinates)):
        for j in range(i + 1, len(coordinates)):
            distance = calculate_distance_km(coordinates[i], coordinates[j])
            distances.append(distance)
    
    if not distances:
        return 0.0
    
    # Return average distance as variance measure
    return sum(distances) / len(distances)


def get_coordinate_center(coordinates: List[Tuple[float, float]]) -> Optional[Tuple[float, float]]: