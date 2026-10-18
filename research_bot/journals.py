"""Explicit editorial journal scope, not an unverified impact-factor filter.

Journal identities/aliases are exact matches. Family membership is enumerated
so, e.g., a journal with 'Lancet' in an unrelated title does not qualify.
"""
import re


def journal_key(value: str) -> str:
    return re.sub(r"[\W_]+", "", value.casefold().replace("&", "and"))


CORE = {
    "JOEM": ("Journal of Occupational and Environmental Medicine", "J Occup Environ Med"),
    "JOH": ("Journal of Occupational Health", "J Occup Health"),
    "SJWEH": ("Scandinavian Journal of Work, Environment & Health", "Scand J Work Environ Health"),
    "OEM": ("Occupational and Environmental Medicine", "Occup Environ Med", "BMJ OEM"),
}
CORE_ISSNS = {"JOEM": "1076-2752", "JOH": "1341-9145", "SJWEH": "0355-3140", "OEM": "1351-0711"}
FAMILIES = {
    "Lancet": ["The Lancet", "Lancet", "Lancet (London, England)", "The Lancet Public Health", "The Lancet Digital Health",
               "The Lancet Psychiatry", "The Lancet Planetary Health", "The Lancet Global Health",
               "The Lancet Healthy Longevity", "The Lancet Respiratory Medicine",
               "The Lancet Diabetes & Endocrinology", "The Lancet Neurology", "The Lancet Oncology",
               "The Lancet Infectious Diseases", "The Lancet Haematology", "The Lancet Rheumatology",
               "The Lancet Regional Health - Europe", "The Lancet Regional Health - Americas",
               "The Lancet Regional Health - Western Pacific", "The Lancet Regional Health - Southeast Asia",
               "eClinicalMedicine", "eBioMedicine"],
    "JAMA": ["JAMA", "JAMA : the journal of the American Medical Association", "JAMA Network Open", "JAMA Internal Medicine", "JAMA Psychiatry",
             "JAMA Neurology", "JAMA Cardiology", "JAMA Oncology", "JAMA Pediatrics",
             "JAMA Surgery", "JAMA Dermatology", "JAMA Ophthalmology",
             "JAMA Otolaryngology - Head & Neck Surgery", "JAMA Health Forum"],
    "NEJM": ["New England Journal of Medicine", "The New England Journal of Medicine", "N Engl J Med", "NEJM Evidence", "NEJM AI"],
    "Other": ["BMJ", "The BMJ", "BMJ (Clinical research ed.)", "Annals of Internal Medicine", "Ann Intern Med",
              "Nature Medicine", "Nature Mental Health", "Nature Human Behaviour", "Nature Aging",
              "Nature Communications", "PLOS Medicine", "American Journal of Respiratory and Critical Care Medicine",
              "European Heart Journal", "Circulation", "Diabetes Care", "Environmental Health Perspectives",
              "Environment International", "Journal of Health Economics", "Labour Economics",
              "Journal of Labor Economics", "Work, Employment and Society", "Work & Stress"],
}
ALIASES = {journal_key(alias): group for group, aliases in CORE.items() for alias in aliases}
ALIASES.update({journal_key(alias): group for group, aliases in FAMILIES.items() for alias in aliases})


def journal_group(title: str) -> str | None:
    return ALIASES.get(journal_key(title))


def eligible_journal(title: str) -> bool:
    return journal_group(title) is not None


def journal_query(titles: list[str]) -> str:
    return "(" + " OR ".join('JOURNAL:"' + t.replace('"', '') + '"' for t in titles) + ")"


def searches() -> list[tuple[str, str, bool]]:
    result = [(name, f'(ISSN:{CORE_ISSNS[name]} OR {journal_query(list(aliases))})', True)
              for name, aliases in CORE.items()]
    # Split large families into bounded independent queries to avoid long URLs.
    for group, titles in FAMILIES.items():
        for start in range(0, len(titles), 8):
            result.append((group, journal_query(titles[start:start + 8]), False))
    return result
