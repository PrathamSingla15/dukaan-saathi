"""Research-grounded seed data for ``inventory.db`` (catalog & stock).

This module is pure, deterministic and import-safe — no network, no file IO and
no heavy dependencies (stdlib ``datetime`` / ``random`` only). It is consumed by
the loader in :mod:`dukaan.db`, which reads :data:`SUPPLIERS`, :data:`CATALOG`
and calls :func:`generate_purchases`.

Everything is modelled on a real mid-2026 Indian kirana (post "GST 2.0" of
22-Sep-2025, when the 12% slab was scrapped and most everyday groceries/FMCG
landed at 5%). Brands, MRPs, pack sizes, per-category retailer margins, shelf
lives, HSN codes and restock cadences are grounded in current market data:

* Margins: staples/oil ~3-8%, dairy ~6-12%, biscuits/chocolate ~10-15%,
  snacks/namkeen ~12-20%, personal/home care ~12-22%, bottled water highest.
* Shelf life: dairy/bread/eggs 1-14 days, biscuits/snacks/chocolate 3-12 months,
  staples/oil/spices 6-24 months, salt/honey near-permanent.
* HSN: loose/unbranded staples 0% GST (so ``hsn`` left ``None``), branded
  pre-packaged items carry their HSN string.

``CATALOG`` deliberately includes a handful of items at/under their reorder
level (a restock is "due"), a few very-short-life perishables, and a believable
spread of fast movers (milk, bread, eggs, atta, biscuits, tea) vs slow movers
(dry fruits, baby formula, specialty oils, premium chocolate).
"""

from __future__ import annotations

import datetime as dt
import random
from typing import Optional

# --------------------------------------------------------------------------- types
# A SUPPLIER dict:  {"name", "phone", "focus"}
# A CATALOG dict:   {"name","category","brand","unit","mrp","purchase_price",
#                    "shelf_life_days","reorder_level","hsn","supplier","qty"}


# ====================================================================== suppliers
# Distributor / super-stockist layer. A busy kirana deals with one focused
# distributor per major brand house plus a couple of local mandi/cash-and-carry
# suppliers for loose staples, eggs and dairy. ~15 here.
SUPPLIERS: list[dict] = [
    {"name": "Sharma Distributors", "phone": "98110 22451",
     "focus": "ITC, Aashirvaad & Sunfeast — atta, biscuits, noodles, spices"},
    {"name": "Gupta Trading Co.", "phone": "98100 73388",
     "focus": "HUL — soaps, detergents, shampoo, tea (Surf, Lifebuoy, Brooke Bond)"},
    {"name": "Verma Agencies", "phone": "99581 40927",
     "focus": "Nestle — Maggi, KitKat, Cerelac, coffee, dairy whitener"},
    {"name": "Amul Parlour Depot", "phone": "98184 61203",
     "focus": "Amul — milk, butter, ghee, paneer, cheese, dahi (cold chain)"},
    {"name": "Mother Dairy Booth", "phone": "98112 55719",
     "focus": "Mother Dairy — token milk, dahi, lassi, paneer (daily)"},
    {"name": "Britannia Super Stockist", "phone": "98715 30844",
     "focus": "Britannia — bread, rusk, Good Day, Marie, Bourbon, cheese"},
    {"name": "Mondelez Beat Sales", "phone": "99102 88471",
     "focus": "Cadbury — Dairy Milk, Five Star, Gems, Bournvita, Oreo"},
    {"name": "PepsiCo Snacks Van", "phone": "98990 71562",
     "focus": "Lay's, Kurkure, Doritos, Quaker, Tropicana (chips & snacks)"},
    {"name": "Varun Beverages", "phone": "98109 64430",
     "focus": "Pepsi, 7Up, Mirinda, Sting, Aquafina (cold drinks & water)"},
    {"name": "Hindustan Beverages", "phone": "98711 20655",
     "focus": "Coca-Cola, Sprite, Thums Up, Limca, Maaza, Kinley"},
    {"name": "Marico D2R", "phone": "99996 41207",
     "focus": "Saffola, Parachute, Nihar — edible oils & hair oil (app order)"},
    {"name": "Patel Wholesale Mandi", "phone": "98115 90034",
     "focus": "loose staples — rice, dal, sugar, atta, poha, besan, masala"},
    {"name": "Rajesh Egg Supply", "phone": "98919 67120",
     "focus": "table eggs, poultry — daily crate delivery"},
    {"name": "Ganesh Provision Stores", "phone": "98101 33572",
     "focus": "Tata, Everest, MDH, Haldiram — salt, spices, namkeen, dry fruits"},
    {"name": "Singh Cash & Carry", "phone": "98738 41906",
     "focus": "Colgate, Dabur, P&G, pooja, stationery, batteries, tobacco"},
]

_SUPPLIER_NAMES = {s["name"] for s in SUPPLIERS}


# =================================================================== catalog build
def _sku(
    name: str,
    category: str,
    brand: str,
    unit: str,
    mrp: float,
    margin: float,
    shelf_life_days: Optional[int],
    reorder_level: int,
    qty: int,
    hsn: Optional[str],
    supplier: str,
) -> dict:
    """Build one CATALOG row. ``purchase_price`` = mrp * (1 - margin), i.e. the
    retailer buys this fraction below MRP (margin is the gross retail margin for
    the category). Rounded to a sensible paisa-free / 0.05 grid."""
    pp = round(mrp * (1.0 - margin), 2)
    return {
        "name": name,
        "category": category,
        "brand": brand,
        "unit": unit,
        "mrp": round(float(mrp), 2),
        "purchase_price": pp,
        "shelf_life_days": shelf_life_days,
        "reorder_level": int(reorder_level),
        "hsn": hsn,
        "supplier": supplier,
        "qty": int(qty),
    }


# Raw SKU table. Tuple order:
#   name, category, brand, unit, mrp, margin, shelf_life_days,
#   reorder_level, qty, hsn, supplier
# margin = gross retail margin (fraction of MRP kept). shelf_life_days=None for
# non-perishables we don't date-track (salt/sugar/stationery/water/tobacco etc.).
_RAW: list[tuple] = [
    # ----------------------------------------------------------- 1. Staples / grains
    ("Aashirvaad Atta 5kg", "Staples", "Aashirvaad", "5kg pack", 330, 0.06, 180, 8, 22, "1101", "Sharma Distributors"),
    ("Fortune Chakki Fresh Atta 5kg", "Staples", "Fortune", "5kg pack", 295, 0.06, 180, 5, 12, "1101", "Patel Wholesale Mandi"),
    ("Loose Chakki Atta", "Staples", "Local", "1kg loose", 42, 0.10, 150, 20, 35, None, "Patel Wholesale Mandi"),
    ("India Gate Basmati Classic 1kg", "Staples", "India Gate", "1kg pack", 198, 0.07, 540, 6, 14, "1006", "Patel Wholesale Mandi"),
    ("Sona Masoori Rice", "Staples", "Local", "1kg loose", 62, 0.08, 365, 25, 48, None, "Patel Wholesale Mandi"),
    ("Tata Sampann Toor Dal 1kg", "Staples", "Tata Sampann", "1kg pack", 244, 0.08, 365, 6, 13, "0713", "Ganesh Provision Stores"),
    ("Loose Toor Dal", "Staples", "Local", "1kg loose", 148, 0.09, 300, 15, 26, None, "Patel Wholesale Mandi"),
    ("Tata Sampann Moong Dal 500g", "Staples", "Tata Sampann", "500g pack", 107, 0.09, 365, 6, 12, "0713", "Ganesh Provision Stores"),
    ("Loose Masoor Dal", "Staples", "Local", "1kg loose", 105, 0.09, 300, 12, 9, None, "Patel Wholesale Mandi"),  # LOW stock
    ("Rajma Chitra", "Staples", "Local", "1kg loose", 160, 0.10, 300, 8, 14, None, "Patel Wholesale Mandi"),
    ("Madhur Sugar 1kg", "Staples", "Madhur", "1kg pack", 65, 0.07, None, 12, 30, "1701", "Patel Wholesale Mandi"),
    ("Loose Sugar", "Staples", "Local", "1kg loose", 46, 0.08, None, 30, 55, None, "Patel Wholesale Mandi"),
    ("Tata Salt 1kg", "Staples", "Tata", "1kg pack", 30, 0.10, None, 15, 40, "2501", "Ganesh Provision Stores"),
    ("Loose Poha (Flattened Rice)", "Staples", "Local", "1kg loose", 70, 0.10, 240, 8, 15, None, "Patel Wholesale Mandi"),
    ("Loose Besan (Gram Flour)", "Staples", "Local", "1kg loose", 90, 0.10, 180, 8, 13, None, "Patel Wholesale Mandi"),

    # --------------------------------------------------------- 2. Edible oils & ghee
    ("Fortune Sunlite Sunflower Oil 1L", "Edible Oil", "Fortune", "1L pouch", 165, 0.05, 270, 10, 24, "1512", "Marico D2R"),
    ("Saffola Gold Oil 1L", "Edible Oil", "Saffola", "1L pouch", 208, 0.05, 270, 8, 18, "1512", "Marico D2R"),
    ("Saffola Gold Oil 5L", "Edible Oil", "Saffola", "5L jar", 1279, 0.04, 270, 3, 6, "1512", "Marico D2R"),
    ("Fortune Mustard Oil 1L", "Edible Oil", "Fortune", "1L pouch", 175, 0.05, 270, 8, 20, "1514", "Marico D2R"),
    ("Fortune Mustard Oil 5L", "Edible Oil", "Fortune", "5L jar", 919, 0.04, 270, 3, 5, "1514", "Marico D2R"),
    ("Fortune Refined Soyabean Oil 1L", "Edible Oil", "Fortune", "1L pouch", 150, 0.05, 270, 6, 13, "1507", "Marico D2R"),
    ("Amul Pure Ghee 1L", "Edible Oil", "Amul", "1L tin", 610, 0.06, 240, 5, 11, "0405", "Amul Parlour Depot"),
    ("Amul Pure Ghee 500ml", "Edible Oil", "Amul", "500ml tin", 315, 0.06, 240, 6, 14, "0405", "Amul Parlour Depot"),
    ("Patanjali Cow Ghee 1L", "Edible Oil", "Patanjali", "1L jar", 650, 0.07, 240, 3, 6, "0405", "Ganesh Provision Stores"),

    # --------------------------------------------------------------------- 3. Dairy
    ("Amul Gold Milk 500ml", "Dairy", "Amul", "500ml pouch", 34, 0.07, 3, 30, 40, "0401", "Amul Parlour Depot"),
    ("Amul Taaza Milk 500ml", "Dairy", "Amul", "500ml pouch", 28, 0.07, 3, 30, 36, "0401", "Amul Parlour Depot"),
    ("Amul Gold Milk 1L", "Dairy", "Amul", "1L pouch", 67, 0.07, 3, 20, 22, "0401", "Amul Parlour Depot"),
    ("Mother Dairy Full Cream Milk 500ml", "Dairy", "Mother Dairy", "500ml pouch", 34, 0.07, 3, 25, 28, "0401", "Mother Dairy Booth"),
    ("Mother Dairy Token Milk 1L", "Dairy", "Mother Dairy", "1L pouch", 60, 0.06, 2, 20, 7, "0401", "Mother Dairy Booth"),  # LOW + very short life
    ("Amul Taaza UHT Milk 1L (Tetra)", "Dairy", "Amul", "1L tetra", 79, 0.08, 150, 8, 16, "0401", "Amul Parlour Depot"),
    ("Amul Butter 100g", "Dairy", "Amul", "100g pack", 58, 0.08, 180, 12, 26, "0405", "Amul Parlour Depot"),
    ("Amul Fresh Paneer 200g", "Dairy", "Amul", "200g pack", 99, 0.08, 6, 8, 10, "0406", "Amul Parlour Depot"),
    ("Mother Dairy Dahi 400g", "Dairy", "Mother Dairy", "400g cup", 45, 0.09, 14, 12, 18, "0403", "Mother Dairy Booth"),
    ("Amul Masti Dahi 200g", "Dairy", "Amul", "200g cup", 25, 0.09, 14, 12, 20, "0403", "Amul Parlour Depot"),
    ("Amul Cheese Slices 100g", "Dairy", "Amul", "100g pack", 85, 0.12, 180, 5, 11, "0406", "Amul Parlour Depot"),
    ("Nestle Everyday Dairy Whitener 400g", "Dairy", "Nestle", "400g pack", 245, 0.08, 365, 4, 8, "0402", "Verma Agencies"),

    # ------------------------------------------------------------- 4. Spices / masala
    ("MDH Garam Masala 100g", "Spices", "MDH", "100g box", 108, 0.12, 365, 6, 14, "0910", "Ganesh Provision Stores"),
    ("Everest Garam Masala 100g", "Spices", "Everest", "100g box", 106, 0.12, 365, 6, 13, "0910", "Ganesh Provision Stores"),
    ("Everest Turmeric (Haldi) Powder 200g", "Spices", "Everest", "200g pack", 80, 0.13, 540, 6, 16, "0910", "Ganesh Provision Stores"),
    ("Catch Red Chilli Powder 100g", "Spices", "Catch", "100g pack", 60, 0.13, 365, 6, 12, "0904", "Ganesh Provision Stores"),
    ("Everest Coriander (Dhania) Powder 100g", "Spices", "Everest", "100g pack", 45, 0.13, 365, 6, 13, "0909", "Ganesh Provision Stores"),
    ("Everest Chhole Masala 100g", "Spices", "Everest", "100g box", 78, 0.13, 365, 4, 8, "0910", "Ganesh Provision Stores"),
    ("Tata Sampann Cumin (Jeera) Whole 100g", "Spices", "Tata Sampann", "100g pack", 90, 0.12, 730, 5, 11, "0909", "Ganesh Provision Stores"),
    ("Loose Mustard Seeds (Rai)", "Spices", "Local", "100g loose", 25, 0.15, 730, 6, 14, None, "Patel Wholesale Mandi"),
    ("Catch Hing (Asafoetida) 25g", "Spices", "Catch", "25g box", 70, 0.13, 540, 3, 7, "1301", "Ganesh Provision Stores"),
    ("Sendha Namak (Rock Salt)", "Spices", "Local", "200g pack", 40, 0.15, None, 5, 12, "2501", "Ganesh Provision Stores"),

    # ----------------------------------------------------------- 5. Biscuits & bakery
    ("Parle-G Biscuit Rs5", "Biscuits", "Parle", "55g pack", 5, 0.11, 240, 60, 130, "1905", "Patel Wholesale Mandi"),
    ("Parle-G Biscuit Rs10", "Biscuits", "Parle", "140g pack", 10, 0.11, 240, 40, 96, "1905", "Patel Wholesale Mandi"),
    ("Parle-G Gold 1kg", "Biscuits", "Parle", "1kg pack", 160, 0.11, 240, 6, 14, "1905", "Sharma Distributors"),
    ("Britannia Good Day Cashew 100g", "Biscuits", "Britannia", "100g pack", 35, 0.12, 270, 12, 28, "1905", "Britannia Super Stockist"),
    ("Britannia Marie Gold 250g", "Biscuits", "Britannia", "250g pack", 40, 0.12, 270, 10, 22, "1905", "Britannia Super Stockist"),
    ("Britannia Bourbon 150g", "Biscuits", "Britannia", "150g pack", 35, 0.13, 270, 10, 20, "1905", "Britannia Super Stockist"),
    ("Sunfeast Dark Fantasy Choco Fills", "Biscuits", "Sunfeast", "75g pack", 35, 0.13, 240, 8, 18, "1905", "Sharma Distributors"),
    ("Parle Hide & Seek 100g", "Biscuits", "Parle", "100g pack", 35, 0.13, 240, 8, 17, "1905", "Sharma Distributors"),
    ("Oreo Vanilla Cream 120g", "Biscuits", "Cadbury", "120g pack", 40, 0.13, 270, 8, 15, "1905", "Mondelez Beat Sales"),
    ("Britannia Rusk Premium Bake 200g", "Biscuits", "Britannia", "200g pack", 45, 0.12, 120, 8, 16, "1905", "Britannia Super Stockist"),

    # -------------------------------------------------- 6. Bread & eggs (perishable)
    ("Britannia White Bread 400g", "Bread", "Britannia", "400g loaf", 45, 0.13, 5, 12, 16, "1905", "Britannia Super Stockist"),
    ("Britannia Brown Bread 400g", "Bread", "Britannia", "400g loaf", 55, 0.13, 6, 8, 5, "1905", "Britannia Super Stockist"),  # LOW + short
    ("Modern Milk Bread 400g", "Bread", "Modern", "400g loaf", 50, 0.13, 5, 6, 10, "1905", "Britannia Super Stockist"),
    ("Britannia Pav 6pc", "Bread", "Britannia", "6 pcs pack", 35, 0.13, 4, 6, 12, "1905", "Britannia Super Stockist"),
    ("Table Eggs (loose)", "Eggs", "Local", "per egg", 7, 0.14, 12, 60, 90, None, "Rajesh Egg Supply"),
    ("Eggs Tray (30)", "Eggs", "Local", "30 tray", 200, 0.12, 12, 8, 14, None, "Rajesh Egg Supply"),
    ("Brown Eggs 6-pack", "Eggs", "Local", "6 pack", 72, 0.13, 12, 6, 9, None, "Rajesh Egg Supply"),

    # ----------------------------------------------------------- 7. Snacks & namkeen
    ("Lay's Classic Salted Rs20", "Snacks", "Lay's", "52g pack", 20, 0.14, 120, 24, 56, "2005", "PepsiCo Snacks Van"),
    ("Lay's Magic Masala Rs10", "Snacks", "Lay's", "26g pack", 10, 0.14, 120, 30, 70, "2005", "PepsiCo Snacks Van"),
    ("Kurkure Masala Munch Rs20", "Snacks", "Kurkure", "82g pack", 20, 0.15, 120, 24, 52, "2106", "PepsiCo Snacks Van"),
    ("Kurkure Rs10", "Snacks", "Kurkure", "38g pack", 10, 0.15, 120, 30, 64, "2106", "PepsiCo Snacks Van"),
    ("Bingo Mad Angles Rs20", "Snacks", "Bingo", "66g pack", 20, 0.15, 120, 16, 30, "2005", "Sharma Distributors"),
    ("Haldiram Aloo Bhujia 200g", "Snacks", "Haldiram", "200g pack", 55, 0.16, 150, 12, 26, "2106", "Ganesh Provision Stores"),
    ("Haldiram Aloo Bhujia 1kg", "Snacks", "Haldiram", "1kg pack", 230, 0.15, 150, 4, 7, "2106", "Ganesh Provision Stores"),
    ("Haldiram Navratan Mixture 350g", "Snacks", "Haldiram", "350g pack", 90, 0.16, 150, 6, 11, "2106", "Ganesh Provision Stores"),
    ("Balaji Wafers Masala 45g", "Snacks", "Balaji", "45g pack", 10, 0.18, 120, 20, 44, "2005", "Ganesh Provision Stores"),

    # ---------------------------------------------- 8. Tea / coffee / health drinks
    ("Tata Tea Premium 250g", "Tea & Coffee", "Tata Tea", "250g pack", 145, 0.10, 540, 10, 24, "0902", "Gupta Trading Co."),
    ("Tata Tea Premium 500g", "Tea & Coffee", "Tata Tea", "500g pack", 280, 0.10, 540, 6, 12, "0902", "Gupta Trading Co."),
    ("Red Label Tea 250g", "Tea & Coffee", "Brooke Bond", "250g pack", 120, 0.10, 540, 10, 22, "0902", "Gupta Trading Co."),
    ("Red Label Tea 500g", "Tea & Coffee", "Brooke Bond", "500g pack", 232, 0.10, 540, 6, 11, "0902", "Gupta Trading Co."),
    ("Nescafe Classic 50g Jar", "Tea & Coffee", "Nescafe", "50g jar", 165, 0.10, 540, 6, 14, "0901", "Verma Agencies"),
    ("Bru Instant Coffee 50g", "Tea & Coffee", "Bru", "50g jar", 155, 0.10, 540, 5, 10, "0901", "Gupta Trading Co."),
    ("Bournvita 500g Jar", "Tea & Coffee", "Cadbury", "500g jar", 245, 0.11, 365, 5, 12, "1806", "Mondelez Beat Sales"),
    ("Horlicks Classic Malt 500g", "Tea & Coffee", "Horlicks", "500g jar", 280, 0.11, 365, 4, 9, "1901", "Gupta Trading Co."),

    # ---------------------------------------------- 9. Cold drinks & packaged water
    ("Coca-Cola 750ml", "Cold Drinks", "Coca-Cola", "750ml PET", 40, 0.10, 180, 24, 48, "2202", "Hindustan Beverages"),
    ("Coca-Cola 250ml", "Cold Drinks", "Coca-Cola", "250ml PET", 20, 0.10, 180, 30, 60, "2202", "Hindustan Beverages"),
    ("Thums Up 750ml", "Cold Drinks", "Thums Up", "750ml PET", 40, 0.10, 180, 24, 44, "2202", "Hindustan Beverages"),
    ("Thums Up 2L", "Cold Drinks", "Thums Up", "2L PET", 99, 0.09, 180, 8, 16, "2202", "Hindustan Beverages"),
    ("Sprite 750ml", "Cold Drinks", "Sprite", "750ml PET", 40, 0.10, 180, 18, 32, "2202", "Hindustan Beverages"),
    ("Pepsi 750ml", "Cold Drinks", "Pepsi", "750ml PET", 40, 0.10, 180, 18, 30, "2202", "Varun Beverages"),
    ("Sting Energy Drink 250ml", "Cold Drinks", "Sting", "250ml PET", 20, 0.12, 180, 18, 40, "2202", "Varun Beverages"),
    ("Maaza Mango 600ml", "Cold Drinks", "Maaza", "600ml PET", 40, 0.11, 180, 12, 24, "2009", "Hindustan Beverages"),
    ("Frooti Mango 250ml", "Cold Drinks", "Frooti", "250ml tetra", 15, 0.13, 270, 18, 36, "2009", "Singh Cash & Carry"),
    ("Bisleri Water 1L", "Water", "Bisleri", "1L bottle", 20, 0.22, None, 24, 48, "2201", "Varun Beverages"),
    ("Bisleri Water 2L", "Water", "Bisleri", "2L bottle", 30, 0.22, None, 12, 28, "2201", "Varun Beverages"),
    ("Bisleri Water 500ml", "Water", "Bisleri", "500ml bottle", 10, 0.25, None, 30, 60, "2201", "Varun Beverages"),
    ("Kinley Water 20L Can", "Water", "Kinley", "20L can", 80, 0.20, None, 4, 6, "2201", "Hindustan Beverages"),

    # -------------------------------------------------- 10. Confectionery / chocolate
    ("Cadbury Dairy Milk Rs10", "Confectionery", "Cadbury", "13.2g bar", 10, 0.12, 300, 40, 90, "1806", "Mondelez Beat Sales"),
    ("Cadbury Dairy Milk Rs20", "Confectionery", "Cadbury", "34g bar", 20, 0.12, 300, 30, 56, "1806", "Mondelez Beat Sales"),
    ("Cadbury Dairy Milk Silk 60g", "Confectionery", "Cadbury", "60g bar", 80, 0.13, 270, 8, 16, "1806", "Mondelez Beat Sales"),
    ("Cadbury Dairy Milk 200g", "Confectionery", "Cadbury", "200g bar", 250, 0.13, 270, 3, 5, "1806", "Mondelez Beat Sales"),
    ("Cadbury Five Star Rs10", "Confectionery", "Cadbury", "22g bar", 10, 0.13, 300, 30, 64, "1806", "Mondelez Beat Sales"),
    ("Nestle KitKat 4-Finger 37g", "Confectionery", "Nestle", "37g bar", 40, 0.13, 270, 12, 24, "1806", "Verma Agencies"),
    ("Nestle Munch Rs10", "Confectionery", "Nestle", "18g bar", 10, 0.13, 270, 24, 40, "1806", "Verma Agencies"),
    ("Perfetti Alpenliebe (jar)", "Confectionery", "Perfetti", "per candy", 1, 0.20, 365, 100, 280, "1704", "Singh Cash & Carry"),
    ("Cadbury Eclairs (jar)", "Confectionery", "Cadbury", "per candy", 2, 0.18, 365, 80, 190, "1704", "Mondelez Beat Sales"),

    # ------------------------------------------------ 11. Instant / packaged foods
    ("Maggi 2-Min Masala Noodles 70g", "Instant Food", "Maggi", "70g pack", 14, 0.13, 270, 60, 140, "1902", "Verma Agencies"),
    ("Maggi Masala Noodles 4-pack", "Instant Food", "Maggi", "280g pack", 56, 0.12, 270, 20, 44, "1902", "Verma Agencies"),
    ("Maggi Atta Noodles 75g", "Instant Food", "Maggi", "75g pack", 22, 0.13, 270, 12, 24, "1902", "Verma Agencies"),
    ("Yippee Magic Masala Noodles 70g", "Instant Food", "Sunfeast", "70g pack", 14, 0.13, 270, 20, 38, "1902", "Sharma Distributors"),
    ("Maggi Tomato Ketchup 500g", "Instant Food", "Maggi", "500g bottle", 99, 0.12, 365, 8, 16, "2103", "Verma Agencies"),
    ("Kissan Mixed Fruit Jam 200g", "Instant Food", "Kissan", "200g jar", 90, 0.12, 365, 5, 11, "2007", "Gupta Trading Co."),
    ("Kissan Tomato Ketchup 850g", "Instant Food", "Kissan", "850g bottle", 140, 0.12, 365, 5, 9, "2103", "Gupta Trading Co."),
    ("MTR Rava Idli Mix 500g", "Instant Food", "MTR", "500g pack", 95, 0.13, 240, 4, 8, "1106", "Ganesh Provision Stores"),
    ("Mother's Recipe Mango Pickle 300g", "Instant Food", "Mother's Recipe", "300g jar", 85, 0.13, 540, 4, 9, "2001", "Ganesh Provision Stores"),

    # ------------------------------------------------------------ 12. Personal care
    ("Colgate Dental Cream 100g", "Personal Care", "Colgate", "100g tube", 76, 0.15, 730, 12, 26, "3306", "Singh Cash & Carry"),
    ("Colgate Dental Cream 200g", "Personal Care", "Colgate", "200g tube", 130, 0.15, 730, 8, 16, "3306", "Singh Cash & Carry"),
    ("Colgate ZigZag Toothbrush", "Personal Care", "Colgate", "1 pc", 35, 0.20, None, 12, 28, "9603", "Singh Cash & Carry"),
    ("Lifebuoy Soap 100g", "Personal Care", "Lifebuoy", "100g bar", 35, 0.16, 730, 18, 40, "3401", "Gupta Trading Co."),
    ("Lux Soap 100g", "Personal Care", "Lux", "100g bar", 45, 0.16, 730, 14, 30, "3401", "Gupta Trading Co."),
    ("Clinic Plus Shampoo 175ml", "Personal Care", "Clinic Plus", "175ml bottle", 99, 0.16, 540, 8, 17, "3305", "Gupta Trading Co."),
    ("Head & Shoulders 180ml", "Personal Care", "Head & Shoulders", "180ml bottle", 175, 0.17, 540, 5, 11, "3305", "Singh Cash & Carry"),
    ("Clinic Plus Shampoo Sachet Rs3", "Personal Care", "Clinic Plus", "sachet", 3, 0.18, 540, 60, 160, "3305", "Gupta Trading Co."),
    ("Parachute Coconut Oil 200ml", "Personal Care", "Parachute", "200ml bottle", 110, 0.14, 540, 8, 18, "3305", "Marico D2R"),
    ("Dabur Amla Hair Oil 200ml", "Personal Care", "Dabur", "200ml bottle", 120, 0.15, 540, 6, 13, "3305", "Singh Cash & Carry"),
    ("Gillette Guard Razor", "Personal Care", "Gillette", "1 pc", 30, 0.18, None, 8, 18, "8212", "Singh Cash & Carry"),
    ("Whisper Ultra Sanitary Pads (7)", "Personal Care", "Whisper", "7 pads", 95, 0.16, None, 6, 12, "9619", "Singh Cash & Carry"),

    # ---------------------------------------------------------------- 13. Home care
    ("Surf Excel Easy Wash 1kg", "Home Care", "Surf Excel", "1kg pack", 140, 0.13, None, 8, 18, "3402", "Gupta Trading Co."),
    ("Ariel Matic Detergent 1kg", "Home Care", "Ariel", "1kg pack", 230, 0.14, None, 5, 10, "3402", "Singh Cash & Carry"),
    ("Tide Plus 1kg", "Home Care", "Tide", "1kg pack", 110, 0.14, None, 6, 14, "3402", "Singh Cash & Carry"),
    ("Nirma Washing Powder 1kg", "Home Care", "Nirma", "1kg pack", 70, 0.18, None, 8, 16, "3402", "Ganesh Provision Stores"),
    ("Vim Dishwash Bar 200g", "Home Care", "Vim", "200g bar", 20, 0.17, None, 14, 32, "3401", "Gupta Trading Co."),
    ("Vim Dishwash Liquid 500ml", "Home Care", "Vim", "500ml bottle", 120, 0.16, None, 6, 13, "3402", "Gupta Trading Co."),
    ("Harpic Toilet Cleaner 500ml", "Home Care", "Harpic", "500ml bottle", 95, 0.16, None, 6, 14, "3402", "Singh Cash & Carry"),
    ("Lizol Floor Cleaner 500ml", "Home Care", "Lizol", "500ml bottle", 99, 0.16, None, 6, 12, "3402", "Singh Cash & Carry"),
    ("Good Knight Refill", "Home Care", "Good Knight", "45ml refill", 80, 0.16, None, 6, 13, "3808", "Singh Cash & Carry"),

    # --------------------------------------------------------------- 14. Baby care
    ("Nestle Cerelac Wheat-Apple 300g", "Baby Care", "Cerelac", "300g pack", 280, 0.10, 365, 4, 9, "1901", "Verma Agencies"),
    ("Nestle Nan Pro Stage 1 400g", "Baby Care", "Nan", "400g tin", 650, 0.09, 540, 3, 5, "1901", "Verma Agencies"),
    ("Pampers Diapers M (8)", "Baby Care", "Pampers", "8 pcs pack", 199, 0.12, None, 4, 8, "9619", "Singh Cash & Carry"),
    ("Huggies Wonder Pants S (9)", "Baby Care", "Huggies", "9 pcs pack", 210, 0.12, None, 3, 6, "9619", "Singh Cash & Carry"),
    ("Johnson's Baby Powder 200g", "Baby Care", "Johnson's", "200g pack", 199, 0.13, 730, 3, 7, "3304", "Singh Cash & Carry"),

    # ----------------------------------------------- 15. Stationery / batteries / bulbs
    ("Classmate Notebook 172pg", "Stationery", "Classmate", "1 notebook", 55, 0.18, None, 10, 22, "4820", "Singh Cash & Carry"),
    ("Reynolds Ball Pen (Blue)", "Stationery", "Reynolds", "1 pen", 10, 0.22, None, 30, 70, "9608", "Singh Cash & Carry"),
    ("Nataraj Pencil 10-pack", "Stationery", "Nataraj", "10 pcs", 50, 0.20, None, 8, 18, "9609", "Singh Cash & Carry"),
    ("Eveready AA Battery (2)", "Stationery", "Eveready", "2 pcs", 40, 0.20, None, 10, 24, "8506", "Singh Cash & Carry"),
    ("Wipro 9W LED Bulb", "Stationery", "Wipro", "1 bulb", 99, 0.18, None, 6, 12, "8539", "Singh Cash & Carry"),
    ("Cello Sticky Tape", "Stationery", "Cello", "1 roll", 25, 0.22, None, 8, 15, "3919", "Singh Cash & Carry"),

    # -------------------------------------------------------- 16. Pooja items / agarbatti
    ("Cycle Agarbatti (Three in One)", "Pooja", "Cycle", "1 pack", 45, 0.18, None, 8, 18, "3307", "Singh Cash & Carry"),
    ("Mangaldeep Agarbatti", "Pooja", "Mangaldeep", "1 pack", 35, 0.18, None, 8, 16, "3307", "Sharma Distributors"),
    ("Camphor (Kapur) 50g", "Pooja", "Mangalam", "50g pack", 60, 0.20, None, 5, 12, "3307", "Singh Cash & Carry"),
    ("Cotton Wicks (Batti) pack", "Pooja", "Local", "1 pack", 20, 0.25, None, 6, 14, None, "Singh Cash & Carry"),
    ("Diya / Deepak (clay) 12pc", "Pooja", "Local", "12 pcs", 30, 0.30, None, 4, 9, None, "Singh Cash & Carry"),
    ("Matchbox (10 boxes)", "Pooja", "Ship", "10 boxes", 20, 0.20, None, 10, 22, "3605", "Singh Cash & Carry"),

    # ----------------------------------------------------------- 17. Dry fruits (festive)
    ("Almonds (Badam) loose", "Dry Fruits", "Local", "250g pack", 230, 0.14, 365, 5, 11, "0802", "Ganesh Provision Stores"),
    ("Cashews (Kaju) loose", "Dry Fruits", "Local", "250g pack", 280, 0.14, 365, 4, 8, "0801", "Ganesh Provision Stores"),
    ("Raisins (Kishmish) loose", "Dry Fruits", "Local", "250g pack", 110, 0.15, 365, 5, 12, "0806", "Ganesh Provision Stores"),
    ("Walnuts (Akhrot) loose", "Dry Fruits", "Local", "250g pack", 320, 0.14, 365, 3, 5, "0802", "Ganesh Provision Stores"),
    ("Makhana (Fox Nuts) 100g", "Dry Fruits", "Local", "100g pack", 90, 0.16, 365, 4, 10, "0802", "Ganesh Provision Stores"),
    ("Khajoor (Dates) 250g", "Dry Fruits", "Local", "250g pack", 120, 0.15, 240, 4, 9, "0804", "Ganesh Provision Stores"),

    # ----------------------------------------------------------------- 18. Tobacco
    ("Gold Flake Kings 10s", "Tobacco", "Gold Flake", "10 sticks", 220, 0.06, None, 10, 22, "2402", "Singh Cash & Carry"),
    ("Wills Navy Cut 10s", "Tobacco", "Wills", "10 sticks", 120, 0.06, None, 10, 18, "2402", "Singh Cash & Carry"),
    ("Classic Regular 10s", "Tobacco", "Classic", "10 sticks", 200, 0.06, None, 8, 14, "2402", "Singh Cash & Carry"),
    ("Bidi Bundle", "Tobacco", "Local", "1 bundle", 25, 0.08, None, 12, 26, "2403", "Singh Cash & Carry"),
    ("Rajnigandha Paan Masala Rs10", "Tobacco", "Rajnigandha", "sachet", 10, 0.10, 365, 20, 40, "2106", "Singh Cash & Carry"),
]


def _build_catalog() -> list[dict]:
    cat = [
        _sku(name, category, brand, unit, mrp, margin, sl, ro, qty, hsn, supplier)
        for (name, category, brand, unit, mrp, margin, sl, ro, qty, hsn, supplier) in _RAW
    ]
    # Integrity guards (cheap, catch data-entry slips at import in dev/tests).
    seen: set[str] = set()
    for it in cat:
        assert it["name"] not in seen, f"duplicate SKU name: {it['name']}"
        seen.add(it["name"])
        assert it["supplier"] in _SUPPLIER_NAMES, f"unknown supplier: {it['supplier']}"
        assert it["purchase_price"] < it["mrp"], f"non-positive margin: {it['name']}"
    return cat


CATALOG: list[dict] = _build_catalog()


# ============================================================= restock / purchases
# Restock cadence (days between distributor deliveries) by category. Perishables
# come daily; fast packaged ~weekly; staples weekly-biweekly; salt/spices/durables
# monthly. This drives how many purchase events each SKU gets over the window.
_CADENCE_DAYS: dict[str, int] = {
    "Dairy": 1,
    "Bread": 1,
    "Eggs": 1,
    "Staples": 10,
    "Edible Oil": 12,
    "Spices": 24,
    "Biscuits": 8,
    "Snacks": 8,
    "Tea & Coffee": 12,
    "Cold Drinks": 7,
    "Water": 7,
    "Confectionery": 9,
    "Instant Food": 8,
    "Personal Care": 16,
    "Home Care": 16,
    "Baby Care": 20,
    "Stationery": 30,
    "Pooja": 21,
    "Dry Fruits": 18,
    "Tobacco": 5,
}
_DEFAULT_CADENCE = 14


def _iso_dt(d: dt.date, rnd: random.Random) -> str:
    """A plausible delivery timestamp: distributor vans run ~07:00-19:00."""
    hour = rnd.randint(7, 19)
    minute = rnd.choice((0, 5, 10, 15, 20, 30, 40, 45, 50))
    return dt.datetime(d.year, d.month, d.day, hour, minute).isoformat(timespec="seconds")


def generate_purchases(
    catalog: list[dict],
    end_date: dt.date,
    seed: int = 7,
    days: int = 120,
) -> list[dict]:
    """Deterministic restock history over the trailing ``days`` (~120) window.

    Each event represents one distributor delivery of a single SKU and is a dict::

        {"item_name", "supplier", "qty", "cost", "ts"}

    ``qty`` is a realistic batch size (scaled to the SKU's reorder level), ``cost``
    is the TOTAL batch cost (qty x a per-unit cost jittered around
    ``purchase_price``), and ``ts`` is an ISO datetime string. Restock frequency
    follows :data:`_CADENCE_DAYS` (perishables daily, staples weekly, etc.) with a
    mild festive bump if a Diwali-window date falls inside the range. Uses a local
    ``random.Random(seed)`` so it never perturbs global RNG state.
    """
    rnd = random.Random(seed)
    start = end_date - dt.timedelta(days=days)

    # Festive window: a Diwali-style spike (~Oct 20 -> Nov 5) drives bigger, more
    # frequent restocks of festive categories if it overlaps the window.
    festive_cats = {
        "Staples", "Edible Oil", "Dry Fruits", "Confectionery", "Snacks",
        "Dairy", "Spices", "Pooja",
    }

    def _is_festive(d: dt.date) -> bool:
        return (d.month == 10 and d.day >= 20) or (d.month == 11 and d.day <= 5)

    out: list[dict] = []
    for it in catalog:
        name = it["name"]
        supplier = it["supplier"]
        pp = float(it["purchase_price"])
        reorder = max(1, int(it.get("reorder_level", 1)))
        cadence = _CADENCE_DAYS.get(it["category"], _DEFAULT_CADENCE)
        category = it["category"]

        # Walk the window in cadence-sized steps, jittering each delivery date so
        # the history doesn't look like a metronome.
        day = rnd.randint(0, cadence)  # random phase offset per SKU
        while day < days:
            jitter = rnd.randint(-1, 1)
            d = start + dt.timedelta(days=min(days, max(0, day + jitter)))

            # Batch size: roughly enough to refill from reorder level back up to a
            # few multiples of it, with per-delivery noise. Perishables order small
            # and often; durables order in bigger lumps.
            base = reorder * rnd.uniform(1.2, 3.0)
            if it["shelf_life_days"] is not None and it["shelf_life_days"] <= 14:
                base = reorder * rnd.uniform(0.8, 1.6)  # short life -> lean batches
            if category in festive_cats and _is_festive(d):
                base *= rnd.uniform(1.5, 2.5)  # festive stock-up
            qty = max(1, int(round(base)))

            unit_cost = pp * rnd.uniform(0.96, 1.02)  # wholesale wobble around pp
            cost = round(unit_cost * qty, 2)

            out.append({
                "item_name": name,
                "supplier": supplier,
                "qty": qty,
                "cost": cost,
                "ts": _iso_dt(d, rnd),
            })
            day += cadence

    out.sort(key=lambda r: r["ts"])
    return out


# --------------------------------------------------------------- tiny self-summary
def category_counts() -> dict[str, int]:
    """SKU count per category (handy for tests / sanity checks)."""
    counts: dict[str, int] = {}
    for it in CATALOG:
        counts[it["category"]] = counts.get(it["category"], 0) + 1
    return counts


if __name__ == "__main__":  # pragma: no cover - manual inspection only
    print(f"SUPPLIERS: {len(SUPPLIERS)}")
    print(f"CATALOG:   {len(CATALOG)} SKUs across {len(category_counts())} categories")
    for c, n in sorted(category_counts().items()):
        print(f"  {c:<16} {n}")
    purchases = generate_purchases(CATALOG, dt.date.today())
    print(f"purchases: {len(purchases)} restock events over 120 days")
    low = [it["name"] for it in CATALOG if it["qty"] <= it["reorder_level"]]
    print(f"low-stock (qty<=reorder): {len(low)} -> {low}")
