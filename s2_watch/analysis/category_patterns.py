"""Watch-form patterns for each news category, built and checked against real feeder traffic.

Each pattern is a case-insensitive regular expression. They seed the built-in "All applications"
category watch forms and give the offline analyzer the same vocabulary, so a message a category
form captures is also classified in that category. Regenerate with care: every term was checked
for false matches in ham traffic (e.g. "WHO" the word, "BILL" the name, "SNAP" the snapshot,
"deploy" an antenna, "HR 01:00Z" a time).
"""

CATEGORY_PATTERNS: dict[str, str] = {
    'Civil Unrest': (
        r'\b(?:riot(?:s|ing|ers?|ous)?|riot\s+(?:police|gear)|protest(?:s|ers?|ors?|ing|ed)?|(?:counter|'
        r'anti)[-\s]?protest(?:s|ers?|ors?)?|protest\s+march(?:es)?|demonstrat(?:ions|ors?)|marchers|'
        r'unrest|civil\s+(?:disorder|disturbances?|tensions?)|loot(?:ing|ers?|ed)|curfews?|'
        r'tear\s*gas(?:sed)?|rubber\s+bullets?|pepper\s*(?:spray(?:ed)?|balls?)|flash\s*bangs?|'
        r'less[-\s]lethal|unlawful\s+assembl(?:y|ies)|dispersal\s+orders?|rall(?:y|ies)(?!\s+points?)|'
        r'sit[-\s]ins?|mobs?|uprisings?|insurrection(?:s|ists?)?|no\s+kings|may\s+day\s+(?:protests?|'
        r'rallies|marches|actions?)|(?:angry|violent|large|hostile|unruly)\s+crowds?|agitators?|'
        r'national\s+guard\s+(?:deployed|deploying|activated|called\s+(?:in|up|out)|mobiliz\w+|troops)|'
        r'blocking\s+(?:roads?|streets?|highways?|traffic|intersections?|freeways?))\b'
    ),
    'Climate / Environment': (
        r'\b(?:thunder\s*storms?|hail(?:storms?)?|tornado(?:es|s)?|flood(?:ing|s)?|flash\s*flood(?:s|'
        r'ing)?|lightning|severe\s+weather|hazardous\s+weather|hurricanes?|tropical\s+storms?|typhoons?|'
        r'cyclones?|blizzards?|snow\s*storms?|ice\s*storms?|freezing\s+rain|sleet|heavy\s+rain|'
        r'high\s+winds?|wind\s+gusts?|straight[-\s]line\s+winds?|microbursts?|derechos?|dust\s*storms?|'
        r'sand\s*storms?|extreme\s+heat|heat\s+waves?|extreme\s+cold|cold\s+snaps?|droughts?|dense\s+fog|'
        r'low\s+visibility|(?:excessive|extreme|high|intense|dangerous|oppressive)\s+heat|'
        r'heat\s*(?:warnings?|advisor(?:y|ies)|watch(?:es)?|index|indices|waves?|emergenc(?:y|ies)|domes?|'
        r'exhaustion|strokes?|illness(?:es)?|related)|record\s+(?:high|low|heat|cold)s?|'
        r'triple[-\s]digits?|(?:bitter(?:ly)?|extreme|dangerous|severe)\s+cold|cold\s+(?:waves?|fronts?|'
        r'air|temps?|temperatures?|weather|advisor(?:y|ies)|warnings?|spells?)|wind\s*chills?|(?:hard\s+|'
        r'killing\s+)?freeze\s+(?:warnings?|watch(?:es)?)|hard\s+freeze|frost\s+advisor(?:y|ies)|(?:sub|'
        r'below)[-\s]?(?:freezing|zero)|freezing\s+temp(?:erature)?s?|arctic\s+(?:blast|air|front|'
        r'outbreak|cold)|polar\s+vortex|hypotherm\w*|frostbite|winter\s+(?:storms?|weather)|'
        r'snow\s*(?:fall|ing|accumulations?|expected|emergenc(?:y|ies)|squalls?)|icy\s+(?:roads?|'
        r'conditions|streets?|highways?)|black\s+ice|ice\s+(?:accumulations?|covered))\b|'
        r'(?<![!#\w.])(?:9\d|1[0-2]\d)(?:\.\d)?\s*(?:°\s*f?|deg(?:ree|ress)?s?(?:\s*f)?|'
        r'f)(?![a-z0-9!\[/])|(?<![!#\w.])(?:-\d{1,2}|[0-2]?\d|3[0-2])(?:\.\d)?\s*(?:°\s*f?|deg(?:ree|'
        r'ress)?s?(?:\s*f)?|f)(?![a-z0-9!\[/])|(?<![!#\w.])\d+(?:-\d+)?\s*(?:\"|'
        r'in(?:ch(?:es)?)?\.?)\s+(?:of\s+)?snow\b|\b(?:wild\s*fires?|(?:brush|forest|grass|'
        r'range)\s*fires?|red\s+flag\s+warnings?|evacuat(?:e|ed|es|ing|ion|ions)|earthquakes?|quakes?|'
        r'aftershocks?|tsunamis?|volcan(?:o|oes|ic)|eruptions?|landslides?|mudslides?|avalanches?|smoke|'
        r'smoky|air\s+quality|aqi|chemical\s+(?:spills?|leaks?|releases?|fires?)|hazmat|oil\s+spills?|'
        r'toxic|boil\s+(?:water|order|notice)s?|fema|disaster\s+declarations?|state\s+of\s+emergency)\b'
    ),
    'Crime': (
        r'\b(?:shootings?|shooters?|shots\s+fired|gunfire|gun\s*m[ae]n|(?:was|were|been|fatally|officer|'
        r'deputy|trooper)\s+shot|stabb(?:ed|ing|ings)|murder(?:s|ed|er|ers)?|homicides?|killed|robber(?:y|'
        r'ies)|robbed|burglar(?:y|ies|s)?|break-ins?|theft|thie(?:f|ves)|stolen|carjack(?:ed|ing|ings)?|'
        r'kidnap(?:ped|ping|pings)?|abduct(?:ed|ion)|amber\s+alert|missing\s+(?:person|child|juvenile|'
        r'hiker|teen|woman|man|girl|boy)s?|assault(?:s|ed)?|arrest(?:s|ed)?|suspects?|fugitives?|manhunt|'
        r'police\s+(?:chase|pursuit|standoff)|swat|hostages?|arson|vandal(?:s|ism|ized)|fraud|scam(?:s|'
        r'mers?)?|cyber\s*attacks?|ransomware|hack(?:ed|ers?)|cartels?)\b'
    ),
    'Economy': (
        r'\b(?:inflation|recession|unemployment|layoffs?|laid\s+off|job\s+cuts|stock\s+market|dow\s+jones|'
        r'nasdaq|markets?\s+(?:crash|plunge|fall|drop|tumble)\w*|bank\s+(?:failures?|runs?|collapses?)|'
        r'interest\s+rates?|federal\s+reserve|the\s+fed|gdp|tariffs?|(?:gas|fuel|food|egg|'
        r'grocery)\s+prices?|prices?\s+(?:up|rising|rise|increase|hike|jump|spike)\w*|fuel\s+going\s+up|'
        r'crude\s+(?:oil|prices?)|brent|oil\s+prices?|economy|economic|snap[-\s]?ebt|snap\s+benefits|'
        r'food\s+stamps|bankrupt(?:cy|cies)?|price\s+instability)\b'
    ),
    'Health / Wellness': (
        r'\b(?:outbreaks?|epidemic|pandemic|covid(?:-?19)?|measles|flu|influenza|h5n1|bird\s+flu|'
        r'avian\s+flu|ebola|mpox|monkeypox|tuberculosis|norovirus|salmonella|e\.?\s*coli|listeria|'
        r'\w*virus(?:es)?|food\s+recalls?|recalled|world\s+health\s+organization|cdc|hospitali[sz]ed|'
        r'hospitals?|icu|ambulances?|medical\s+emergenc(?:y|ies)|mass\s+casualt(?:y|ies)|injur(?:ed|ies)|'
        r'overdoses?|fentanyl|quarantin(?:e|ed)|vaccin(?:e|es|ation|ations)|diseases?|infections?|'
        r'illness(?:es)?|pathogens?)\b'
    ),
    'Infrastructure': (
        r'\b(?:power\s+(?:outages?|out|failures?|restored|loss)|outages?|blackouts?|brownouts?|'
        r'grid\s+(?:down|failures?|outages?|stress)|without\s+power|no\s+power|substations?|'
        r'transformers?\s+(?:blew|fire|explo\w+)|(?:power\s+)?lines?\s+down|downed\s+(?:power\s+)?lines?|'
        r'water\s+(?:main|outage|pressure|service)s?|boil\s+water|sewer|dams?\s+(?:failures?|breach\w*|'
        r'alert)|levees?|bridges?\s+(?:closed|collapsed?|out|washed\s+out)|road\s+closures?|'
        r'roads?\s+(?:closed|out|washed\s+out|blocked)|(?:highway|interstate|i-\d+)\s+(?:closed|'
        r'shut\s*down)|closes\s+i-\d+|internet\s+(?:outages?|down)|cell\s+(?:service|towers?)\s+(?:down|'
        r'out)|communications?\s+(?:outages?|down|failures?)|911\s+(?:outage|down)|phone\s+(?:lines?|'
        r'service)\s+(?:down|out)|gas\s+leaks?|pipelines?|refiner(?:y|ies)|derail(?:ed|ment|ments)|'
        r'airports?\s+(?:closed|closures?)|ground\s+stops?|flights?\s+(?:cancel\w*|delay\w*)|'
        r'voting\s+machines\s+down)\b'
    ),
    'Military Conflict': (
        r'\b(?:military|air\s*strikes?|missiles?|drone\s+(?:strikes?|attacks?)|drones|artillery|shelling|'
        r'invasion|invade[sd]?|troops|ceasefire|war|warfare|combat|naval|navy|carrier\s+(?:group|'
        r'strike\s+group)|destroyers?|submarines?|nato|pentagon|defcon|nuclear|icbms?|hypersonic|'
        r'ukrain(?:e|ian)|russia(?:n|ns)?|israel(?:i|is)?|gaza|hamas|hezbollah|houthis?|iran(?:ian|ians|'
        r's)?|taiwan|north\s+korea(?:n)?|idf|pla\s+(?:navy|forces|drills?)|(?:troops?|forces|'
        r'carriers?)\s+deploy\w*|deploy(?:s|ed|ing)?\s+(?:troops|forces|carriers?)|mobiliz(?:e|ed|es|ing|'
        r'ation))\b'
    ),
    'Politics': (
        r'\b(?:elections?|vot(?:e|es|ing|ers?)|ballots?|polls?\s+(?:show|close|open|closing)|'
        r'polling\s+(?:places?|stations?)|congress(?:ional|man|woman)?|senate|senators?|legislat(?:ion|'
        r'ive|ure|ors?)|(?:sb|hb)\s*\d{2,}(?:-\d+)?|(?:senate|house)\s+bills?|bills?\s+(?:would|passed|'
        r'passes|signed|introduced)|governor|president(?:ial)?|white\s+house|trump|biden|harris|vance|'
        r'executive\s+orders?|supreme\s+court|scotus|impeach(?:ed|ment)?|government\s+shutdown|political|'
        r'politics|politicians?|campaign|democrats?(?!\s+republic)|democratic(?!\s+republic)|republicans?|'
        r'gop|mayor|capitol)\b'
    ),
    'Supply Chain': (
        r'\b(?:shortages?|supply\s+chains?|out\s+of\s+stock|empty\s+shelves|shelves\s+(?:empty|bare)|'
        r'panic\s+buy(?:ing)?|hoarding|rationing|port\s+(?:closures?|closed|strikes?|congestion)|'
        r'shipping\s+(?:delays?|disruptions?)|shipments?\s+delay\w*|freight|truckers?|trucking|'
        r'rail\s+strike|dock\s*workers?|longshore\w*|fuel\s+(?:shortages?|lines?|rationing)|gas\s+lines|'
        r'chokepoints?|hormuz|suez|panama\s+canal|red\s+sea|backorder(?:ed|s)?|food\s+(?:supply|'
        r'supplies))\b'
    ),
    'Terrorism': (
        r'\b(?:terror(?:ism|ist|ists)?|bomb\s+threats?|bomb(?:s|ing|ings|er|ers)?|vbied|ieds?|explosions?|'
        r'explosives?|detonat(?:e|ed|ion)|suicide\s+(?:bomb\w*|attack\w*)|jihad(?:i|ist|ists)?|isis|isil|'
        r'islamic\s+state|al[-\s]?qaeda|extremis[mt]s?|radicali[sz](?:ed|ation)|mass\s+shootings?|'
        r'active\s+shooters?|lone\s+wolf|(?:vehicle|ramming)\s+attacks?|(?:synagogue|church|mosque|school|'
        r'mall|concert|stadium)\s+attacks?|attacks?\s+on\s+(?:a\s+|the\s+)?(?:synagogue|church|mosque|'
        r'school|police|civilians)|beheading|sleeper\s+cells?|jttf|ntas|threat\s+level|elevated\s+threat|'
        r'hoax\s+calls?)\b'
    ),
}

# The environmental-hazard half of the Climate / Environment pattern (the other half is the
# Weather form). The analyzer uses this half; its weather rules are tiered separately, so a
# bare temperature reading is captured by the form without being flagged as news.
ENVIRONMENTAL_HAZARDS_PATTERN = (
    r'\b(?:wild\s*fires?|(?:brush|forest|grass|range)\s*fires?|red\s+flag\s+warnings?|evacuat(?:e|ed|'
    r'es|ing|ion|ions)|earthquakes?|quakes?|aftershocks?|tsunamis?|volcan(?:o|oes|ic)|eruptions?|'
    r'landslides?|mudslides?|avalanches?|smoke|smoky|air\s+quality|aqi|chemical\s+(?:spills?|leaks?|'
    r'releases?|fires?)|hazmat|oil\s+spills?|toxic|boil\s+(?:water|order|notice)s?|fema|'
    r'disaster\s+declarations?|state\s+of\s+emergency)\b'
)
