"""Which trusted sources to search for each claim category.

`searchers` are sites we query directly through their own search API, in order.
`domains` are the trusted sites we ask Tavily to stay within when the direct sources
don't produce enough evidence. `india_domains` are put first when a claim is about India.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CategorySources:
    searchers: tuple[str, ...]
    domains: tuple[str, ...]
    india_domains: tuple[str, ...] = ()


# Indian fact-checkers, useful for every category.
INDIA_FACT_CHECKERS = (
    "boomlive.in",
    "altnews.in",
    "factchecker.in",
    "vishvasnews.com",
    "newschecker.in",
    "thequint.com",
    "pib.gov.in",
)
INDIA_NEWS = ("thehindu.com", "indianexpress.com", "pti.in")
GLOBAL_NEWS = ("reuters.com", "apnews.com", "bbc.com")
GLOBAL_FACT_CHECKERS = ("snopes.com", "factcheck.org", "fullfact.org", "factcheck.afp.com")

TRUSTED_SOURCES: dict[str, CategorySources] = {
    "health": CategorySources(
        ("factcheck", "wikipedia"),
        (
            "who.int",
            "cdc.gov",
            "nih.gov",
            "nhs.uk",
            "mayoclinic.org",
            "medlineplus.gov",
            "cochrane.org",
            "fda.gov",
            "ecdc.europa.eu",
            "clevelandclinic.org",
            "thelancet.com",
            "nejm.org",
        ),
        (
            "mohfw.gov.in",
            "icmr.gov.in",
            "nhp.gov.in",
            "cdsco.gov.in",
            "aiims.edu",
            "fssai.gov.in",
            *INDIA_FACT_CHECKERS,
        ),
    ),
    "science": CategorySources(
        ("factcheck", "wikipedia"),
        (
            "nasa.gov",
            "esa.int",
            "nature.com",
            "science.org",
            "nih.gov",
            "noaa.gov",
            "usgs.gov",
            "ipcc.ch",
            "cern.ch",
            "nationalgeographic.com",
            "scientificamerican.com",
            "newscientist.com",
        ),
        (
            "isro.gov.in",
            "dst.gov.in",
            "csir.res.in",
            "imd.gov.in",
            "iisc.ac.in",
            "drdo.gov.in",
            *INDIA_FACT_CHECKERS,
        ),
    ),
    "history": CategorySources(
        ("wikipedia",),
        (
            "britannica.com",
            "history.com",
            "smithsonianmag.com",
            "loc.gov",
            "archives.gov",
            "nationalarchives.gov.uk",
            "worldhistory.org",
        ),
        (
            "asi.nic.in",
            "nationalarchives.nic.in",
            "indiaculture.gov.in",
            "indianculture.gov.in",
            "ncert.nic.in",
            *INDIA_FACT_CHECKERS,
        ),
    ),
    "politics": CategorySources(
        ("factcheck", "wikipedia"),
        ("politifact.com", *GLOBAL_FACT_CHECKERS, *GLOBAL_NEWS),
        (
            "eci.gov.in",
            "indiacode.nic.in",
            "legislative.gov.in",
            "sansad.in",
            "prsindia.org",
            "sci.gov.in",
            *INDIA_FACT_CHECKERS,
            *INDIA_NEWS,
        ),
    ),
    "economy": CategorySources(
        ("wikipedia",),
        (
            "worldbank.org",
            "imf.org",
            "oecd.org",
            "bls.gov",
            "fred.stlouisfed.org",
            "ecb.europa.eu",
            *GLOBAL_NEWS,
        ),
        (
            "rbi.org.in",
            "mospi.gov.in",
            "finmin.nic.in",
            "sebi.gov.in",
            "niti.gov.in",
            "indiabudget.gov.in",
            "incometax.gov.in",
            *INDIA_FACT_CHECKERS,
            *INDIA_NEWS,
        ),
    ),
    "technology": CategorySources(
        ("wikipedia",),
        ("nist.gov", "w3.org", "ieee.org", "acm.org", "arxiv.org", "arstechnica.com", "reuters.com"),
        (
            "meity.gov.in",
            "trai.gov.in",
            "uidai.gov.in",
            "cert-in.org.in",
            "digitalindia.gov.in",
            *INDIA_FACT_CHECKERS,
        ),
    ),
    "sports": CategorySources(
        ("wikipedia",),
        (
            "olympics.com",
            "olympedia.org",
            "espn.com",
            "fifa.com",
            "worldathletics.org",
            "icc-cricket.com",
            "bbc.com",
        ),
        (
            "bcci.tv",
            "espncricinfo.com",
            "sai.gov.in",
            "olympic.ind.in",
            "hockeyindia.org",
            *INDIA_FACT_CHECKERS,
        ),
    ),
    "general": CategorySources(
        ("factcheck", "wikipedia"),
        ("britannica.com", *GLOBAL_FACT_CHECKERS, *GLOBAL_NEWS),
        (*INDIA_FACT_CHECKERS, *INDIA_NEWS, "india.gov.in"),
    ),
}


def sources_for(category: str) -> CategorySources:
    return TRUSTED_SOURCES.get(category, TRUSTED_SOURCES["general"])


def domains_for(category: str, india_related: bool) -> list[str]:
    """Tavily domains for a claim: Indian sources first for India-related claims."""
    sources = sources_for(category)
    ordered = [*sources.india_domains, *sources.domains] if india_related else [*sources.domains]
    return list(dict.fromkeys(ordered))
