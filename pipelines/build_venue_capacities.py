"""Build the venue_capacities reference table for the Silver layer.

Setlist.fm doesn't carry expected_attendance, so the model has no way to learn
the relationship between event *magnitude* and economic impact unless we
attach a capacity number to each venue. This script does that:

1. Reads the list of CA venues seen in `aai540_silver.setlistfm_setlists`
   (cached locally to `data/curated/_ca_venues_raw.csv`; regenerate via the
   Athena query in `sql/silver/ca_venues_for_capacity_lookup.sql`).
2. Joins against the hand-curated `SEED` dict in this file (Wikipedia,
   operator websites, festival capacities) to attach known capacities.
3. Defaults unmatched venues to 500 (median small-CA-club capacity).
4. Writes `data/curated/venue_capacities.csv` — the canonical reference table.

The Bayesian impact model uses `capacity * sell_through` as a measurement of
event attendance, with sell_through as a latent parameter the model learns
(or a fixed 0.80 prior for a simpler v1).

Re-run when:
- New venues appear in the setlistfm data (new CA pulls)
- We want to refine specific capacity values
"""
from __future__ import annotations

from pathlib import Path

import polars as pl

CURATED   = Path("data/curated")
RAW_CSV   = CURATED / "_ca_venues_raw.csv"      # input: from Athena query
FINAL_CSV = CURATED / "venue_capacities.csv"    # output: canonical reference

DEFAULT_CAPACITY = 500  # median small-club capacity, used when no seed matches

# Capacity dict keyed by (venue_name, city_name). Sources: Wikipedia infoboxes
# and operator websites, sanity-checked against known-show photos. For multi-config
# venues (e.g., stadiums) we use the typical concert capacity, not max-seated.
SEED: dict[tuple[str, str], tuple[int, str]] = {
    # ---- Major arenas / stadiums (10K+) ----
    ("Kia Forum", "Inglewood"):                     (17500, "wikipedia"),
    ("Hollywood Bowl", "Los Angeles"):              (17500, "wikipedia"),
    ("Dodger Stadium", "Los Angeles"):              (50000, "concert_typical"),
    ("Chase Center", "San Francisco"):              (18000, "wikipedia"),
    ("Crypto.com Arena", "Los Angeles"):            (20000, "wikipedia"),
    ("Honda Center", "Anaheim"):                    (17000, "wikipedia"),
    ("SoFi Stadium", "Inglewood"):                  (70000, "wikipedia"),
    ("BMO Stadium", "Los Angeles"):                 (22000, "wikipedia"),
    ("Greek Theatre", "Los Angeles"):               (5900, "wikipedia"),
    ("Greek Theatre", "Berkeley"):                  (8500, "wikipedia"),
    ("Shrine Auditorium", "Los Angeles"):           (6300, "wikipedia"),
    ("Frost Amphitheater", "Stanford"):             (6900, "wikipedia"),
    ("YouTube Theater", "Inglewood"):               (6000, "wikipedia"),
    ("Pechanga Arena", "San Diego"):                (12000, "wikipedia"),
    ("Toyota Arena", "Ontario"):                    (11000, "wikipedia"),
    ("SAP Center", "San Jose"):                     (17500, "wikipedia"),
    ("SAP Center at San Jose", "San Jose"):         (17500, "wikipedia"),
    ("Acrisure Arena", "Palm Desert"):              (11000, "wikipedia"),
    ("Oakland Arena", "Oakland"):                   (19500, "wikipedia"),
    ("Shoreline Amphitheatre", "Mountain View"):    (22500, "wikipedia"),
    ("Bill Graham Civic Auditorium", "San Francisco"): (8500, "wikipedia"),
    ("Pacific Amphitheatre", "Costa Mesa"):         (8500, "wikipedia"),
    ("FivePoint Amphitheatre", "Irvine"):           (12000, "operator"),
    ("North Island Credit Union Amphitheatre", "Chula Vista"): (20000, "wikipedia"),
    ("Petco Park", "San Diego"):                    (42000, "wikipedia"),
    ("Microsoft Theater", "Los Angeles"):           (7100, "wikipedia"),
    ("Golden 1 Center", "Sacramento"):              (17000, "wikipedia"),
    ("William Randolph Hearst Greek Theatre", "Berkeley"): (8500, "wikipedia"),

    # ---- Mid-size theaters (1.5-5K) ----
    ("Wiltern Theatre", "Los Angeles"):             (1850, "wikipedia"),
    ("The Wiltern", "Los Angeles"):                 (1850, "wikipedia"),
    ("Hollywood Palladium", "Los Angeles"):         (4000, "wikipedia"),
    ("Fox Theater", "Oakland"):                     (2800, "wikipedia"),
    ("The Fox Theater", "Oakland"):                 (2800, "wikipedia"),
    ("The Warfield", "San Francisco"):              (2300, "wikipedia"),
    ("The Belasco", "Los Angeles"):                 (1500, "wikipedia"),
    ("The Fonda Theatre", "Los Angeles"):           (1200, "wikipedia"),
    ("House of Blues", "Anaheim"):                  (2200, "operator"),
    ("House of Blues", "San Diego"):                (1100, "operator"),
    ("The Regency Ballroom", "San Francisco"):      (1400, "operator"),
    ("The Masonic", "San Francisco"):               (3300, "wikipedia"),
    ("Masonic Auditorium", "San Francisco"):        (3300, "wikipedia"),
    ("The Novo", "Los Angeles"):                    (2300, "wikipedia"),
    ("Saban Theatre", "Beverly Hills"):             (1900, "wikipedia"),
    ("The Theatre at Ace Hotel", "Los Angeles"):    (1600, "operator"),
    ("Los Angeles Theatre", "Los Angeles"):         (2000, "operator"),
    ("SOMA", "San Diego"):                          (2500, "operator"),
    ("Nova SD", "San Diego"):                       (1200, "operator"),
    ("Humphreys", "San Diego"):                     (1400, "operator"),
    ("1720", "Los Angeles"):                        (1500, "operator"),
    ("Goldfield Trading Post", "Roseville"):        (1000, "operator"),
    ("Goldfield Trading Post", "Sacramento"):       (1500, "operator"),
    ("Ace of Spades", "Sacramento"):                (1000, "wikipedia"),
    ("August Hall", "San Francisco"):               (950, "operator"),
    ("Ventura Theater", "Ventura"):                 (1200, "operator"),
    ("The Majestic Ventura Theater", "Ventura"):    (1200, "operator"),
    ("Cal Coast Credit Union Open Air Theatre", "San Diego"): (4600, "wikipedia"),
    ("The Mountain Winery", "Saratoga"):            (2500, "operator"),
    ("Garden Amphitheatre", "Garden Grove"):        (1300, "operator"),
    ("The UC Theatre Taube Family Music Hall", "Berkeley"): (1400, "operator"),

    # ---- Small clubs (200-1500) ----
    ("The Fillmore", "San Francisco"):              (1150, "wikipedia"),
    ("Great American Music Hall", "San Francisco"): (600, "wikipedia"),
    ("The Catalyst", "Santa Cruz"):                 (800, "operator"),
    ("The Catalyst Atrium", "Santa Cruz"):          (350, "operator"),
    ("The Chapel", "San Francisco"):                (600, "operator"),
    ("Bottom of the Hill", "San Francisco"):        (250, "wikipedia"),
    ("The Independent", "San Francisco"):           (500, "operator"),
    ("Slim's", "San Francisco"):                    (500, "operator"),
    ("Belly Up Tavern", "Solana Beach"):            (600, "operator"),
    ("The Echo", "Los Angeles"):                    (350, "operator"),
    ("Echoplex", "Los Angeles"):                    (700, "operator"),
    ("The Roxy", "West Hollywood"):                 (500, "wikipedia"),
    ("Roxy Theatre", "West Hollywood"):             (500, "wikipedia"),
    ("Troubadour", "West Hollywood"):               (500, "wikipedia"),
    ("Whisky A Go Go", "West Hollywood"):           (500, "wikipedia"),
    ("Lodge Room", "Los Angeles"):                  (350, "operator"),
    ("The Regent Theater", "Los Angeles"):          (1000, "operator"),
    ("The Glass House", "Pomona"):                  (800, "operator"),
    ("The Observatory", "Santa Ana"):               (1000, "operator"),
    ("The Observatory North Park", "San Diego"):    (1100, "operator"),
    ("Brick by Brick", "San Diego"):                (300, "operator"),
    ("The Coach House", "San Juan Capistrano"):     (480, "operator"),
    ("Yoshi's Oakland", "Oakland"):                 (310, "operator"),
    ("Blue Note Napa", "Napa"):                     (175, "operator"),
    ("Largo at the Coronet", "Los Angeles"):        (280, "operator"),
    ("Zebulon Cafe", "Los Angeles"):                (200, "operator"),
    ("2220 Arts + Archives", "Los Angeles"):        (200, "operator"),
    ("El Rey Theatre", "Los Angeles"):              (750, "wikipedia"),
    ("Teragram Ballroom", "Los Angeles"):           (600, "operator"),
    ("Cornerstone", "Berkeley"):                    (700, "operator"),
    ("Constellation Room", "Santa Ana"):            (350, "operator"),
    ("Sweetwater Music Hall", "Mill Valley"):       (300, "operator"),
    ("Chain Reaction", "Anaheim"):                  (350, "operator"),
    ("Rickshaw Stop", "San Francisco"):             (500, "operator"),
    ("Starline Social Club", "Oakland"):            (500, "operator"),
    ("Moroccan Lounge", "Los Angeles"):             (275, "operator"),
    ("The Casbah", "San Diego"):                    (200, "wikipedia"),
    ("Harlow's Restaurant & Nightclub", "Sacramento"): (200, "operator"),
    ("Soda Bar", "San Diego"):                      (200, "operator"),
    ("Pappy & Harriet's Palace", "Pioneertown"):    (500, "operator"),
    ("Resident", "Los Angeles"):                    (300, "operator"),
    ("The Hi Hat", "Los Angeles"):                  (250, "operator"),
    ("Hotel Cafe", "Los Angeles"):                  (200, "operator"),
    ("The Mint", "Los Angeles"):                    (200, "operator"),
    ("Felton Music Hall", "Felton"):                (350, "operator"),
    ("The New Parish", "Oakland"):                  (400, "operator"),
    ("The Starlet Room", "Sacramento"):             (200, "operator"),
    ("Music Box", "San Diego"):                     (700, "operator"),
    ("Sound Nightclub", "Los Angeles"):             (700, "operator"),
    ("Avalon Hollywood", "Los Angeles"):            (1500, "operator"),
    ("Exchange LA", "Los Angeles"):                 (1500, "operator"),
    ("Hangar 1018", "Los Angeles"):                 (1500, "operator"),
    ("Academy LA", "Los Angeles"):                  (1500, "operator"),
    ("Madame Siam", "Los Angeles"):                 (300, "operator"),
    ("The Guild Theatre", "Menlo Park"):            (600, "operator"),
    ("Ventura Music Hall", "Ventura"):              (500, "operator"),
    ("The Canyon", "Agoura Hills"):                 (450, "operator"),
    ("The Canyon - Montclair", "Montclair"):        (450, "operator"),
    ("Hopmonk Tavern", "Novato"):                   (300, "operator"),

    # ---- Outdoor festival sites (typical major-fest config) ----
    ("Discovery Park", "Sacramento"):               (30000, "festival_aftershock"),
    ("Golden Gate Park", "San Francisco"):          (75000, "festival_outsidelands"),
    ("Pier 80", "San Francisco"):                   (30000, "festival_portola"),
    ("Brookside Park", "Pasadena"):                 (30000, "festival_cruelworld"),
    ("Los Angeles State Historic Park", "Los Angeles"): (40000, "festival_hardsummer"),
    ("Moreno Beach", "Perris"):                     (35000, "festival_lakeparty"),
    ("Nevada County Fairgrounds", "Grass Valley"):  (15000, "festival_worldfest"),
    ("Napa Valley Expo", "Napa"):                   (40000, "festival_bottlerock"),
    ("Empire Polo Club", "Indio"):                  (125000, "festival_coachella"),
    ("National Orange Show Events Center", "San Bernardino"): (30000, "festival_capacity"),
    ("Embarcadero Marina Park North", "San Diego"): (10000, "operator"),
    ("Doheny State Beach", "Dana Point"):           (15000, "festival_dohenyblues"),
    ("Seaside Lagoon", "Redondo Beach"):            (5000, "festival_beachlife"),
    ("Oak Canyon Park", "Silverado"):               (15000, "festival_outdoor"),

    # ---- TV studio / cruise / non-traditional (small audiences) ----
    ("The Kelly Clarkson Show", "Universal City"):  (200, "tv_studio_audience"),
    ("The Masked Singer", "Los Angeles"):           (200, "tv_studio_audience"),
    ("Holland America Lines-MS Koningsdam", "San Diego"): (200, "cruise_show_theater"),
    ("Norwegian Jewel", "Los Angeles"):             (200, "cruise_show_theater"),
    ("Carnival Panorama", "Long Beach"):            (200, "cruise_show_theater"),
}


def main() -> int:
    if not RAW_CSV.exists():
        raise SystemExit(
            f"missing input: {RAW_CSV}\n"
            "  regenerate with: aws athena start-query-execution \\\n"
            "    --query-string file://sql/silver/ca_venues_for_capacity_lookup.sql \\\n"
            "    --query-execution-context Database=aai540_silver"
        )

    src = pl.read_csv(RAW_CSV)
    print(f"loaded {src.height} CA venues, {src['n_events'].sum():,} total events")

    seed_df = pl.DataFrame({
        "venue_name":      [k[0] for k in SEED.keys()],
        "city_name":       [k[1] for k in SEED.keys()],
        "capacity":        [v[0] for v in SEED.values()],
        "capacity_source": [v[1] for v in SEED.values()],
    })

    out = src.join(seed_df, on=["venue_name", "city_name"], how="left")
    out = out.with_columns([
        pl.col("capacity").fill_null(DEFAULT_CAPACITY).alias("capacity"),
        pl.col("capacity_source").fill_null("default_small_club").alias("capacity_source"),
    ])
    out = out.select([
        "venue_id", "venue_name", "city_name", "n_events", "n_artists",
        "capacity", "capacity_source",
    ])
    FINAL_CSV.parent.mkdir(parents=True, exist_ok=True)
    out.write_csv(FINAL_CSV)

    seeded    = out.filter(pl.col("capacity_source") != "default_small_club")
    defaulted = out.filter(pl.col("capacity_source") == "default_small_club")
    total_ev  = out["n_events"].sum()

    print(f"\nseeded:    {seeded.height:>4} venues  ({seeded['n_events'].sum():>5,} events,"
          f" {seeded['n_events'].sum() / total_ev:.1%})")
    print(f"defaulted: {defaulted.height:>4} venues  ({defaulted['n_events'].sum():>5,} events,"
          f" {defaulted['n_events'].sum() / total_ev:.1%}) @ {DEFAULT_CAPACITY}")
    print(f"\nwrote {FINAL_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
