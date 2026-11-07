"""The 26 DRC provinces: names as ACLED spells them, boundaries, and VIEWS grid cells assigned to each."""

import json
import math
import unicodedata
from pathlib import Path

PROVINCES = ('Bas-Uele', 'Equateur', 'Haut-Katanga', 'Haut-Lomami', 'Haut-Uele', 'Ituri', 'Kasai', 'Kasai-Central',
             'Kasai-Oriental', 'Kinshasa', 'Kongo-Central', 'Kwango', 'Kwilu', 'Lomami', 'Lualaba', 'Mai-Ndombe',
             'Maniema', 'Mongala', 'Nord-Kivu', 'Nord-Ubangi', 'Sankuru', 'Sud-Kivu', 'Sud-Ubangi', 'Tanganyika',
             'Tshopo', 'Tshuapa')

# geoBoundaries uses English for six provinces; everything else matches once accents are removed.
GEOBOUNDARIES_NAMES = {'Upper Uele': 'Haut-Uele', 'Lower Uele': 'Bas-Uele', 'North Kivu': 'Nord-Kivu',
                       'South Kivu': 'Sud-Kivu', 'Central Kasai': 'Kasai-Central'}


def plain(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))


def acled_name(name):
    name = GEOBOUNDARIES_NAMES.get(name, plain(name))
    if name not in PROVINCES:
        raise ValueError(f'Unknown province name: {name}')
    return name


def load_shapes(path):
    """{province: [polygon, ...]}, each polygon a list of rings of (lon, lat), the first ring the outline."""
    features = json.loads(Path(path).read_text())['features']
    shapes = {}
    for feature in features:
        geometry = feature['geometry']
        polygons = geometry['coordinates'] if geometry['type'] == 'MultiPolygon' else [geometry['coordinates']]
        shapes.setdefault(acled_name(feature['properties']['shapeName']), []).extend(polygons)
    if set(shapes) != set(PROVINCES):
        raise ValueError(f'Boundaries cover {len(shapes)} provinces, not the 26: missing {sorted(set(PROVINCES) - set(shapes))}')
    return shapes


def in_ring(lon, lat, ring):
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def contains(polygons, lat, lon):
    """Even-odd rule over every ring, so holes are excluded."""
    return any(sum(in_ring(lon, lat, ring) for ring in polygon) % 2 == 1 for polygon in polygons)


def nearest(shapes, lat, lon):
    def distance(polygons):
        return min(math.hypot(x - lon, y - lat) for polygon in polygons for ring in polygon for x, y in ring)
    return min(shapes, key=lambda name: distance(shapes[name]))


def assign_cells(cells, shapes, centre):
    """Each VIEWS cell goes to the province containing its centre, or the nearest province for border cells."""
    assigned, border = {}, []
    for gid in sorted(cells):
        lat, lon = centre(gid)
        found = [name for name, polygons in shapes.items() if contains(polygons, lat, lon)]
        if len(found) > 1:
            raise ValueError(f'Cell {gid} falls in several provinces: {found}')
        if found:
            assigned[gid] = found[0]
        else:
            assigned[gid] = nearest(shapes, lat, lon)
            border.append(gid)
    return assigned, border


def simplify(ring, tolerance):
    """Douglas-Peucker on one ring, for a lighter dashboard map. Scores never use the simplified shapes."""
    if len(ring) < 3:
        return ring
    (x1, y1), (x2, y2) = ring[0], ring[-1]
    length = math.hypot(x2 - x1, y2 - y1)
    def gap(point):
        if not length:
            return math.hypot(point[0] - x1, point[1] - y1)
        return abs((y2 - y1) * point[0] - (x2 - x1) * point[1] + x2 * y1 - y2 * x1) / length
    index, far = max(((i, gap(p)) for i, p in enumerate(ring[1:-1], 1)), key=lambda item: item[1])
    if far <= tolerance:
        return [ring[0], ring[-1]]
    return simplify(ring[:index + 1], tolerance)[:-1] + simplify(ring[index:], tolerance)


def map_geojson(shapes, tolerance=.04):
    features = []
    for name, polygons in shapes.items():
        outlines = []
        for polygon in polygons:
            outline = [[round(x, 3), round(y, 3)] for x, y in simplify(polygon[0], tolerance)]
            if len(outline) >= 4:
                outlines.append([outline])
        features.append({'type': 'Feature', 'properties': {'province': name},
                         'geometry': {'type': 'MultiPolygon', 'coordinates': outlines}})
    return {'type': 'FeatureCollection', 'features': features}
