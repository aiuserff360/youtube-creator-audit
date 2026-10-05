"""Keyword lists for language / industry classification.

Keywords are lowercase and matched on word boundaries. An industry named in
the title wins (buckets checked in the order below, Telugu first); otherwise
the industry mentioned most often in description + tags wins, ties going to
the earlier bucket. Extend the lists freely.

Avoid bare words that are ambiguous across industries (e.g. "vijay", "yash",
"vikram", "hindi" on a Hindi-language channel).
"""

BUCKETS = [
    ("Telugu", [
        "telugu", "tollywood", "prabhas", "allu arjun", "jr ntr", "ntr",
        "mahesh babu", "ram charan", "pawan kalyan", "chiranjeevi", "rajamouli",
        "vijay deverakonda", "nani", "balakrishna", "ravi teja", "nithiin",
        "sukumar", "trivikram", "naga chaitanya", "sandeep reddy vanga",
        "venkatesh",
        # additions to the starting list
        "vijay devarakonda", "nagarjuna", "adivi sesh", "sreeleela",
        "pushpa", "baahubali", "bahubali", "rrr", "salaar", "kalki 2898 ad",
        "devara",
    ]),
    ("Tamil", [
        "tamil", "kollywood", "rajinikanth", "thalaivar", "kamal haasan",
        "thalapathy", "vijay sethupathi", "ajith kumar", "suriya", "dhanush",
        "sivakarthikeyan", "chiyaan vikram", "lokesh kanagaraj", "mani ratnam",
        "vetrimaaran", "jailer", "kanguva", "vettaiyan", "ponniyin selvan",
        "thangalaan",
    ]),
    ("Malayalam", [
        "malayalam", "mollywood", "mohanlal", "mammootty", "fahadh faasil",
        "prithviraj sukumaran", "dulquer salmaan", "tovino thomas",
        "nivin pauly", "lijo jose pellissery", "manjummel boys", "aavesham",
        "empuraan",
    ]),
    ("Kannada", [
        "kannada", "sandalwood", "kgf", "k.g.f", "kantara", "rishab shetty",
        "rocking star yash", "kiccha sudeep", "rakshit shetty", "prashanth neel",
        "hombale films", "puneeth rajkumar",
    ]),
    ("Hindi/Bollywood", [
        "bollywood", "shah rukh khan", "shahrukh khan", "srk", "salman khan",
        "aamir khan", "akshay kumar", "ranbir kapoor", "ranveer singh",
        "hrithik roshan", "ajay devgn", "kartik aaryan", "vicky kaushal",
        "shahid kapoor", "tiger shroff", "varun dhawan", "sunny deol",
        "ayushmann khurrana", "rajkummar rao", "deepika padukone", "alia bhatt",
        "karan johar", "rohit shetty", "sanjay leela bhansali",
        "rajkumar hirani", "yash raj films", "yrf", "dharma productions",
    ]),
    ("Hollywood", [
        "hollywood", "marvel", "mcu", "dc", "dceu", "dcu", "avengers",
        "spider-man", "spiderman", "batman", "superman", "deadpool",
        "wolverine", "disney", "pixar", "star wars", "christopher nolan",
        "tom cruise", "mission impossible", "fast and furious", "james cameron",
        "james gunn", "john wick", "jurassic", "warner bros", "oppenheimer",
        "godzilla", "transformers",
    ]),
    ("Korean", [
        "korean", "south korea", "k-drama", "kdrama", "squid game",
        "bong joon ho", "train to busan",
    ]),
    ("Chinese", [
        "chinese", "china", "c-drama", "cdrama", "hong kong", "donghua",
        "jackie chan",
    ]),
    ("OTT/Other", [
        "netflix", "prime video", "amazon prime", "hotstar", "jiohotstar",
        "jiocinema", "zee5", "sonyliv", "apple tv", "web series", "webseries",
        "ott",
    ]),
]

FORMAT_BUCKET = "OTT/Other"  # a format, not an industry: used only when no industry matched
FALLBACK_BUCKET = "Other/Unclear"
