"""Hotspot names, a rule extractor, and the unlearned live X adjustment."""

import hashlib
import math
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone

HORIZONS = (30, 60, 90)
SIGNAL_DECAY_DAYS = 21
TARGET = "at least one ACLED event with 10+ fatalities or a territory change"

# province, aliases. No hindsight-based risk tiers. Boundaries live in config/hotspots.csv.
HOTSPOTS = {
    'Walungu (Kaziba, Muhungu, Kamanyola)': ('South Kivu', ('walungu', 'kaziba', 'muhungu', 'munanira', 'bideka', 'kamanyola', 'nyangezi')),
    'Uvira (Ruzizi plain)': ('South Kivu', ('uvira', 'luvungi', 'katogota', 'sange', 'kiliba', 'luberizi')),
    'Fizi (Minembwe, Hauts Plateaux)': ('South Kivu', ('fizi', 'minembwe', 'kabanja', 'baraka', 'bibokoboko')),
    'Mwenga (Itombwe, Kamituga)': ('South Kivu', ('mwenga', 'itombwe', 'kamituga', 'mikenge')),
    'Kalehe (Minova, Numbi)': ('South Kivu', ('kalehe', 'minova', 'numbi', 'nyabibwe', 'lumbishi')),
    'Kabare / Bukavu': ('South Kivu', ('kabare', 'kavumu', 'katana', 'bukavu')),
    'Shabunda': ('South Kivu', ('shabunda',)),
    'Goma / Nyiragongo': ('North Kivu', ('goma', 'nyiragongo')),
    'Masisi': ('North Kivu', ('masisi', 'sake', 'kitchanga', 'mweso', 'ngungu', 'nyabiondo')),
    'Rutshuru': ('North Kivu', ('rutshuru', 'bwito', 'kiwanja', 'bunagana', 'kanyabayonga', 'tongo')),
    'Walikale': ('North Kivu', ('walikale', 'pinga', 'mpofi')),
    'Lubero / Butembo': ('North Kivu', ('lubero', 'butembo', 'kirumba', 'kipese', 'alimbongo')),
    'Beni': ('North Kivu', ('beni', 'oicha', 'eringeti', 'mbau', 'kamango', 'mangina', 'maboya')),
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


def hotspot_signals(tagged, issued_at=None):
    issued_at = issued_at or datetime.now(timezone.utc)
    cutoff = issued_at - timedelta(hours=48)
    counts = {name: defaultdict(set) for name in HOTSPOTS}
    seen = {name: set() for name in HOTSPOTS}
    for row in tagged:
        if not row['on_topic'] or row['historical'] or row.get('extraction_error'):
            continue
        posted = datetime.fromisoformat(row['created_at'].replace('Z', '+00:00'))
        if posted > issued_at:
            raise ValueError('A post is dated after the forecast issue time.')
        for name in row['hotspots']:
            if row['fingerprint'] in seen[name]:
                continue
            seen[name].add(row['fingerprint'])
            author = row.get('author_id') or row['id']
            spot = counts[name]
            spot['authors'].add(author)
            for signal in SIGNALS:
                if row[signal]:
                    spot[signal].add(author)
            if row['event'] and posted >= cutoff:
                spot['event_last_48h'].add(author)
    return {name: {key: len(spot[key]) for key in ('authors', *SIGNALS, 'event_last_48h')}
            for name, spot in counts.items()}


def signal_multiplier(spot):
    weights = {'event': .25, 'event_last_48h': .15, 'control_change': .35,
               'escalation': .20, 'deescalation': -.30}
    log_m = sum(weight * math.log1p(spot[key]) for key, weight in weights.items())
    return min(6.0, max(.5, math.exp(log_m)))


def adjust(probabilities, multiplier):
    """Adjust cumulative hazards; never multiply probabilities directly."""
    result, previous = {}, 0.0
    for horizon in HORIZONS:
        p = probabilities[horizon]
        average = 1 + (multiplier - 1) * SIGNAL_DECAY_DAYS / horizon * (-math.expm1(-horizon / SIGNAL_DECAY_DAYS))
        changed = 1.0 if p == 1 else -math.expm1(math.log1p(-p) * average)
        previous = max(previous, changed)
        result[horizon] = previous
    return result

