"""Place names, a rule extractor, X author counts per province, and the pre-set X adjustment."""

import hashlib
import math
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone

HORIZONS = {30: 1, 60: 2, 90: 3}  # Days, and the calendar months they cover from the first of the issue month.
SIGNAL_DECAY_DAYS = 21
X_EXPONENT = .5      # Shrinks the ratio of X attention to expected deaths; fixed before any score.
X_LIMITS = (.5, 2.0)

# Hotspot: (province as ACLED spells it, aliases). Used to place posts, not to score.
HOTSPOTS = {
    'Walungu (Kaziba, Muhungu, Kamanyola)': ('Sud-Kivu', ('walungu', 'kaziba', 'muhungu', 'munanira', 'bideka', 'kamanyola', 'nyangezi')),
    'Uvira (Ruzizi plain)': ('Sud-Kivu', ('uvira', 'luvungi', 'katogota', 'sange', 'kiliba', 'luberizi')),
    'Fizi (Minembwe, Hauts Plateaux)': ('Sud-Kivu', ('fizi', 'minembwe', 'kabanja', 'baraka', 'bibokoboko')),
    'Mwenga (Itombwe, Kamituga)': ('Sud-Kivu', ('mwenga', 'itombwe', 'kamituga', 'mikenge')),
    'Kalehe (Minova, Numbi)': ('Sud-Kivu', ('kalehe', 'minova', 'numbi', 'nyabibwe', 'lumbishi')),
    'Kabare / Bukavu': ('Sud-Kivu', ('kabare', 'kavumu', 'katana', 'bukavu')),
    'Shabunda': ('Sud-Kivu', ('shabunda',)),
    'Goma / Nyiragongo': ('Nord-Kivu', ('goma', 'nyiragongo')),
    'Masisi': ('Nord-Kivu', ('masisi', 'sake', 'kitchanga', 'mweso', 'ngungu', 'nyabiondo')),
    'Rutshuru': ('Nord-Kivu', ('rutshuru', 'bwito', 'kiwanja', 'bunagana', 'kanyabayonga', 'tongo')),
    'Walikale': ('Nord-Kivu', ('walikale', 'pinga', 'mpofi')),
    'Lubero / Butembo': ('Nord-Kivu', ('lubero', 'butembo', 'kirumba', 'kipese', 'alimbongo')),
    'Beni': ('Nord-Kivu', ('beni', 'oicha', 'eringeti', 'mbau', 'kamango', 'mangina', 'maboya')),
    'Djugu': ('Ituri', ('djugu', 'pimbo', 'fataki', 'lodda', 'bule', 'savo', 'largu', 'nizi', 'drodro')),
    'Irumu / Bunia': ('Ituri', ('irumu', 'komanda', 'bunia')),
    'Mambasa': ('Ituri', ('mambasa', 'biakato')),
    'Mahagi / Aru': ('Ituri', ('mahagi', 'aru')),
    'Tanganyika': ('Tanganyika', ('tanganyika', 'kalemie', 'nyunzu', 'moba')),
    'Kwamouth (Mobondo)': ('Mai-Ndombe', ('kwamouth', 'mobondo')),
    'Kinshasa (political violence)': ('Kinshasa', ('kinshasa',)),
}

ACTORS = {'M23/AFC': ('m23', 'afc'),
 'FRN': ('frn',),
 'RDF (Rwanda)': ('rdf',),
 'FARDC': ('fardc',),
 'Wazalendo': ('wazalendo',),
 'ADF': ('adf',),
 'CODECO': ('codeco',),
 'FDLR': ('fdlr',),
 'Twirwaneho': ('twirwaneho',),
 'Burundi (FDNB)': ('fdnb', 'burundais', 'burundian'),
 'MONUSCO': ('monusco',)}

SIGNALS = {'event': ('affrontement',
           '\\bcombats?\\b',
           '\\bclash',
           'fighting',
           '\\battaque',
           '\\battack',
           '\\btue(e|s|es)?\\b',
           '\\bkilled\\b',
           '\\bdeaths?\\b',
           '\\bmorts?\\b',
           'massacre',
           'bombard',
           '\\bshell',
           '\\bdrone',
           'enlev',
           'abduct',
           'kidnap',
           '\\botages?\\b',
           'deplace',
           'displac',
           'fuite',
           '\\bfled\\b',
           'incendi',
           '\\bburn'),
 'control_change': ('repris le controle',
                    'reprennent le controle',
                    'reprise (du|de|des)',
                    '\\breprennent\\b',
                    'liber(e|es|ation)\\b',
                    '\\btombe(e)?\\b',
                    '\\bcaptur',
                    '\\bseiz',
                    '\\boccupe',
                    '\\btook control',
                    '\\bfell to\\b',
                    '\\bse rapprochent\\b',
                    '\\bavancee',
                    '\\badvanc'),
 'escalation': ("prepar\\w* (une|l')\\s?offensive",
                '\\boffensive\\b',
                'renforts?',
                'reinforcement',
                'mobilis',
                "distribu\\w* (d'|des )?armes",
                '\\barmes\\b.*distribu',
                '\\bcoalition\\b',
                '\\balliance\\b',
                'ultimatum',
                'encercl',
                '\\bmenace',
                '\\bthreat',
                'recrut',
                'recruit',
                '\\bescalat',
                '\\bsurrounded\\b',
                '\\bmassing\\b',
                'prendre les armes'),
 'deescalation': ('cessez-le-feu',
                  'ceasefire',
                  'cease-fire',
                  '\\btreve\\b',
                  'truce',
                  'accord de paix',
                  'peace (deal|agreement|talks)',
                  'negociation',
                  '\\bnegotiat',
                  '\\bdoha\\b',
                  '\\bretrait\\b',
                  'withdraw',
                  'desarmement',
                  'disarm',
                  'dialogue')}

HISTORICAL = ('\\b(19[5-9]\\d|200\\d|201\\d|202[0-4])\\b.*\\b(attaque|attack|massacre|tue|killed|guerre|war)',
 '\\bans apres\\b',
 '\\byears ago\\b',
 '\\bil y a \\d+ ans\\b',
 'anniversaire',
 'commemor',
 '\\bsouvenir\\b',
 '\\bmemorial\\b',
 '\\bhommage\\b')

# Province names on their own, written as people write them. Matched on normalized text.
PROVINCE_PATTERNS = {
    'Nord-Kivu': (r'\bnord[- ]kivu\b', r'\bnorth[- ]kivu\b'),
    'Sud-Kivu': (r'\bsud[- ]kivu\b', r'\bsouth[- ]kivu\b'),
    'Ituri': (r'\bituri\b',),
    'Tanganyika': (r'\btanganyika\b',),
    'Mai-Ndombe': (r'\bmai[- ]?ndombe\b',),
    'Kinshasa': (r'\bkinshasa\b',),
}
X_PROVINCES = tuple(PROVINCE_PATTERNS)

DRC_CONTEXT = ('kivu', 'ituri', 'rdc', 'congo', 'fardc', 'm23', 'wazalendo', 'codeco', 'adf', 'frn', 'kinshasa')


def normalize(text):
    text = unicodedata.normalize('NFKD', text or '')
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return re.sub(r'https?://\S+', ' ', text.lower()).replace('’', "'")


def words(text):
    return set(re.findall(r'[a-z0-9]+', normalize(text)))


def classify(post):
    text, tokens = normalize(post.get('text')), words(post.get('text'))
    places = [name for name, (_, aliases) in HOTSPOTS.items() if tokens.intersection(aliases)]
    posted = datetime.fromisoformat(post['created_at'].replace('Z', '+00:00'))
    years = [int(y) for y in re.findall(r'\b(?:19|20)\d{2}\b', text)]
    historical = any(re.search(p, text) for p in HISTORICAL[1:])
    historical |= bool(years and max(years) < posted.year and re.search(r'attaque|attack|massacre|killed|guerre|war', text))
    return {
        **{key: post.get(key) for key in ('id', 'author_id', 'created_at', 'text')},
        'fingerprint': hashlib.sha256(text.encode()).hexdigest(),
        'on_topic': bool(places or tokens.intersection(DRC_CONTEXT)),
        'historical': historical, 'hotspots': places,
        'provinces': sorted({HOTSPOTS[name][0] for name in places}
                            | {name for name, patterns in PROVINCE_PATTERNS.items() if any(re.search(p, text) for p in patterns)}),
        'actors': [name for name, aliases in ACTORS.items() if tokens.intersection(aliases)],
        **{name: any(re.search(p, text) for p in patterns) for name, patterns in SIGNALS.items()},
    }


def resolve_hotspot(value):
    if value in HOTSPOTS:
        return value
    for name, (_, aliases) in HOTSPOTS.items():
        if words(value).intersection(aliases):
            return name
    return None


def province_signals(tagged, issued_at, credible_only=False):
    """Distinct authors per province. A post counts for every province it names; each author counts once per signal.

    When `credible_only=True` and rows include an LLM `source_type`, only `field_report` and `news` posts are counted.
    """
    counts = {name: defaultdict(set) for name in X_PROVINCES}
    seen = {name: set() for name in X_PROVINCES}
    for row in tagged:
        if not row['on_topic'] or row['historical'] or row.get('extraction_error'):
            continue
        if credible_only and row.get('source_type') and row['source_type'] not in ('field_report', 'news'):
            continue
        posted = datetime.fromisoformat(row['created_at'].replace('Z', '+00:00'))
        if posted > issued_at:
            raise ValueError('A post is dated after the forecast issue time.')
        provinces = row.get('provinces') or sorted({HOTSPOTS[name][0] for name in row['hotspots'] if name in HOTSPOTS})
        for name in provinces:
            if row['fingerprint'] in seen[name]:
                continue  # Copies of the same text count once.
            seen[name].add(row['fingerprint'])
            author = row.get('author_id') or row['id']
            counts[name]['authors'].add(author)
            for signal in SIGNALS:
                if row[signal]:
                    counts[name][signal].add(author)
    return {name: {key: len(spot[key]) for key in ('authors', *SIGNALS)} for name, spot in counts.items()}


def x_multipliers(event_authors, baseline_deaths):
    """Share of X event authors against share of expected deaths, across the X provinces.

    m = (x_share / baseline_share) ** 0.5, limited to 0.5-2, with one added to every count so a quiet
    province is not divided by zero. X moves expected deaths between provinces; it is not learned.
    """
    names = list(event_authors)
    x_total = sum(event_authors[n] + 1 for n in names)
    b_total = sum(baseline_deaths[n] + 1 for n in names)
    low, high = X_LIMITS
    return {n: min(high, max(low, (((event_authors[n] + 1) / x_total) / ((baseline_deaths[n] + 1) / b_total)) ** X_EXPONENT))
            for n in names}


def decayed(multiplier, days):
    """Average of a multiplier that fades with a 21-day time constant over the first `days` days."""
    return 1 + (multiplier - 1) * SIGNAL_DECAY_DAYS / days * (-math.expm1(-days / SIGNAL_DECAY_DAYS))


def nowcast_from_x(signals, baseline_deaths):
    """Impute the reporting-lag period (M-1 or W-1) per province from X precursor signals and baseline deaths.

    Combines the attention-to-baseline multiplier for ongoing conflict with raw X precursor counts
    (event, control_change, escalation, deescalation) so the spatio-temporal ML model can both
    adjust active provinces and trigger new outbreaks when control_change > 0 in a zero-baseline province.
    """
    events = {name: signals[name]['event'] for name in signals}
    multipliers = x_multipliers(events, {name: baseline_deaths.get(name, 0.0) for name in signals})
    return {
        name: {
            'deaths': baseline_deaths.get(name, 0.0) * multipliers[name],
            'multiplier': multipliers[name],
            **{key: float(signals[name].get(key, 0)) for key in SIGNALS},
        }
        for name in signals
    }
