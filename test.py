import googlemaps
from datetime import datetime

gmaps = googlemaps.Client(key='')

# Geocoding an address
geocode_result = gmaps.geocode('Бзд-26 хороо элезабэт хотхон 214-3-157 тоот 89170417')
print(geocode_result)