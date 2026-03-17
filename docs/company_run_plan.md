# Company Run Plan — BG First Fund

## How the pipeline works best

**Sprouts (485 stores)** — Perfect case. Single website with sitemap containing all store pages. Each store page has the same HTML template with address, coordinates, phone. Pipeline found 485/484 stores, 7 DCs, 1 HQ. Total: 493 assets in ~2 minutes.

**Boral (335 assets)** — Good but harder. Industrial company with varied page types (quarries, concrete plants, asphalt, recycling). Each location page has different detail levels. Pipeline found 335 assets but some noise from product pages getting extracted. Total: 335 assets in ~10 minutes.

**Key factors for an easy run:**
- Single website with `/locations/` or `/stores/` directory
- Individual location pages with consistent template
- Sitemap that includes location pages
- English language
- <1000 locations (manageable scrape time)

---

## Recommended 20 Companies

### Tier 1: Easiest — Retail chains (like Sprouts)

| Company | ISIN | Locations | Website | Notes |
|---|---|---|---|---|
| **Floor & Decor** | US3397501012 | ~270 | flooranddecor.com/store/{city}/{ST}/{zip}/{id} | Perfect. Clean individual pages, manageable count |
| **Dutch Bros** | US26701L1008 | ~1,136 | dutchbros.com/locations/{state}/{city}/{address} | US coffee chain, clean URLs. Also has subdomain |
| **WillScot Holdings** | US9713781048 | ~260 | willscot.com/en/locations/{state}/{city} | Modular buildings, clean state/city URLs |
| **Dollar General** | US2566771059 | ~20,000 | dollargeneral.com/store-directory/{st}/{city}/{id} | **Use Overture first** — already have 18,677 locations. Pipeline only for DCs/HQ |
| **AutoZone** | US0533321024 | ~7,700 | autozone.com/locations/{st}/{city}/{addr}.html | **Use Overture first** — too many for scraping. Pipeline for DCs/HQ |
| **Service Corp Intl** | US8175651046 | ~1,900 | dignitymemorial.com/funeral-homes/{city}-{st} | Funeral homes. Note: locations on Dignity Memorial domain, not sci-corp.com |

### Tier 2: Industrial with location pages (like Boral)

| Company | ISIN | Locations | Website | Notes |
|---|---|---|---|---|
| **Martin Marietta** | US5732841060 | 500+ | martinmarietta.com/locations/{region}/{district}/{name} | Excellent. Clean region > district > facility hierarchy |
| **Stella-Jones** | CA85853F1053 | 40+ | stella-jones.com/en/locations/{name} | Canadian utility poles. Individual plant pages |
| **Comfort Systems USA** | US1999081045 | 184+ | comfortsystemsusa.com/location/{company}/ | HVAC holding company. Also has PDF location list |
| **Petrobras** | US71654V4086 | ~11 refineries | petrobras.com.br/en/quem-somos/refinaria-{name} | Small count, each refinery has own page. Portuguese slugs |
| **Nippon Paint** | JP3749400002 | 118 factories | nipponpaint-holdings.com/en/ir/factory/{code}/ | Factory pages under IR section. Japanese parent |
| **Epiroc** | SE0015658117 | Many | epiroc.com/en-us/contact/service-center-{city} | Per-country URL paths, need to handle each locale |

### Tier 3: Possible but harder

| Company | ISIN | Locations | Website | Notes |
|---|---|---|---|---|
| **Games Workshop** | GB0003718474 | ~570 | warhammer.com store finder | JS-heavy, multiple domains. May need JS rendering |
| **Ensign Group** | US29358P1012 | ~369 | ensigngroup.net/map/ | JS map only, no crawlable directory. Facilities under many subsidiary names |
| **Novo Nordisk** | DK0062498333 | Dozens | novonordisk.com/location.html | Office-oriented, not facility directory. Production info scattered |
| **Nexans** | FR0000044448 | 60-70 plants | nexans.com/group/locations/ | Likely JS map. French company, English available |
| **Eaton** | IE00B8KQN827 | Hundreds | eaton.com (fragmented) | Different facility types under different URL hierarchies |
| **Brunswick Corp** | US1170431092 | Dozens | brunswick.com/our-company/locations | Corporate overview only, no detailed directory |

### Skip — too complex for current pipeline

| Company | ISIN | Why skip |
|---|---|---|
| **CRH** | IE0001827041 | ~4,000 locations across hundreds of subsidiary brands. No centralized directory |
| **Advanced Drainage Systems** | US00790R1041 | JS/API-driven finder, not crawlable |

---

## Recommended Run Order

**Start with these 5 (guaranteed easy):**
1. Floor & Decor (270 stores)
2. WillScot (260 branches)
3. Stella-Jones (40 plants)
4. Petrobras (11 refineries)
5. Dutch Bros (1,136 stores)

**Then these 5 (good but more work):**
6. Martin Marietta (500+ quarries/plants)
7. Comfort Systems USA (184 locations)
8. Service Corp International (1,900 funeral homes)
9. Nippon Paint (118 factories)
10. Dollar General (use Overture → 18,677 stores, pipeline for DCs)

**Then the rest as time allows.**

---

## Overture Maps Strategy

For companies with >1,000 retail locations, **use Overture first**:

```bash
# Extract from Overture places.gpkg
ogr2ogr -nln store -t_srs EPSG:4326 -dialect sqlite \
  -sql "select json_extract('names.common', '$[0].value') as name,
    json_extract('addresses', '$[0].freeform') as address,
    json_extract('addresses', '$[0].locality') as city,
    json_extract('addresses', '$[0].postcode') as zip,
    json_extract('addresses', '$[0].region') as state,
    json_extract('addresses', '$[0].country') as country,
    json_extract('phones', '$[0]') as phone, geometry
  from places
  where json_extract('names.common', '$[0].value') like 'COMPANY NAME'
  and json_extract('websites', '$[0]') like '%website.com%'" \
  output.gpkg places.gpkg
```

Then run the pipeline with `--start-from extract` to find DCs, offices, HQ that Overture doesn't have.

**Already extracted:** Dollar General (18,677 locations in `/Users/nikolai.tennant/Downloads/dollarGeneral.gpkg`)
