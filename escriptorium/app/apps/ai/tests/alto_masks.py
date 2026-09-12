"""Load TextLine polygons from a kraken/ALTO file for preflight fixtures."""
import xml.etree.ElementTree as ET


def load_alto_masks(path):
    root = ET.parse(path).getroot()
    ns = root.tag.split("}")[0][1:]
    P = f"{{{ns}}}"
    masks = []
    for tl in root.iter(f"{P}TextLine"):
        poly = tl.find(f"{P}Shape/{P}Polygon")
        if poly is None:
            continue
        pts = list(map(int, poly.attrib["POINTS"].split()))
        masks.append(list(zip(pts[::2], pts[1::2])))
    return masks
