"""LLM extraction into the same rows as forecast.classify. Inference only; nothing here trains a model.

Results are cached by post, model and prompt version, so re-runs do not pay twice and
changing the prompt starts a fresh cache. Install requirements-llm.txt to use this.
"""

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel, Field

from drc_watch import forecast as fc
from drc_watch.fetch_recent import DATA, load_env

CACHE = DATA / 'llm_cache'
PROMPT_VERSION = 'v2'
FALLBACK_MODELS = {'claude-opus-5-5', 'claude-opus-5', 'claude-sonnet-5-5', 'claude-fable-5-1'}

Hotspot = Literal[tuple(fc.HOTSPOTS)]
Actor = Literal[tuple(fc.ACTORS)]


class PostExtraction(BaseModel):
    relevant: bool = Field(description='About armed conflict, insecurity or political violence in the DRC.')
    historical: bool = Field(description='The main event described happened more than 60 days before the post date, or the post is an anniversary or history thread.')
    event_types: list[Literal['armed_clash', 'attack_on_civilians', 'control_change', 'displacement', 'abduction', 'shelling_or_drone']] = Field(
        description='Violent events the post reports as happening at the post date or in the 60 days before it. Empty for opinion, rhetoric or history.')
    escalation: bool = Field(description='Signals of coming escalation: offensive being prepared, reinforcements, mobilisation, arms distribution, new alliances, ultimatums, encirclement.')
    deescalation: bool = Field(description='Signals of de-escalation: ceasefire, truce, peace talks or agreement, withdrawal, disarmament.')
    hotspots: list[Hotspot] = Field(description='Hotspots where the reported events or signals take place. Only places in the list; leave empty if none apply.')
    other_places: list[str] = Field(description='DRC place names in the post that do not fit any listed hotspot, as written.')
    actors: list[Actor] = Field(description='Armed or security actors involved.')
    fatalities: int | None = Field(description='Deaths reported for the current event, if a number is given.')
    displaced: int | None = Field(description='People reported newly displaced, if a number is given. Convert households to people only if the post does.')
    source_type: Literal['field_report', 'news', 'party_claim', 'opinion', 'other'] = Field(
        description='party_claim = a belligerent or its supporters claiming success or blaming the other side.')


SYSTEM = f"""You extract conflict events from posts on X about the Democratic Republic of the Congo.
Posts are in French, English, Swahili, Kinyarwanda or Lingala, and many are partisan.
Each message starts with the post date. Judge all timing relative to that date, not to today.

Rules:
- Tag what the post reports, not what the author hopes or fears. Accusations and slogans are not events.
- A post whose main event happened more than 60 days before the post date (e.g. the 1996 refugee camp attacks) is historical and gets no event_types.
- "Kinshasa" used to mean the government is not a location. Only tag the Kinshasa hotspot for events in the city itself.
- Map localities to their hotspot. Examples: Kaziba, Muhungu, Kamanyola and the Itara-Luvungi groupement are in Walungu; Luvungi village, Katogota, Sange and Kiliba are in the Uvira plain; Minembwe is in Fizi; Sake and Kitchanga are in Masisi; Pimbo and Fataki are in Djugu; Bunia is in Irumu.
- FRN is the coalition that includes M23/AFC. Tag M23/AFC and FRN separately as written.

Hotspots: {", ".join(fc.HOTSPOTS)}."""


def is_gemini(model):
    return model.startswith('gemini')


def _client(model):
    env = load_env()
    if is_gemini(model):
        from google import genai
        from google.genai import types
        options = types.HttpOptions(timeout=60_000, retry_options=types.HttpRetryOptions(attempts=1))
        key = env.get('GEMINI_API_KEY')
        return genai.Client(api_key=key, http_options=options) if key else genai.Client(http_options=options)
    import anthropic
    key = env.get('ANTHROPIC_API_KEY')
    return anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()


def _user_message(post):
    return f"Posted {post.get('created_at', '')[:10]}:\n\n{post.get('text', '')}"


def _call_gemini(client, post, model, attempts=5):
    from google.genai import errors, types
    # Posts about killings and sexual violence are the signal here; default filters would drop them.
    safety = [types.SafetySetting(category=category, threshold='BLOCK_NONE') for category in
              ('HARM_CATEGORY_HARASSMENT', 'HARM_CATEGORY_HATE_SPEECH', 'HARM_CATEGORY_SEXUALLY_EXPLICIT', 'HARM_CATEGORY_DANGEROUS_CONTENT')]
    config = types.GenerateContentConfig(system_instruction=SYSTEM, response_mime_type='application/json',
                                         response_schema=PostExtraction, safety_settings=safety)
    for attempt in range(attempts):
        try:
            response = client.models.generate_content(model=model, contents=_user_message(post), config=config)
            break
        except errors.APIError as error:
            if error.code not in (429, 500, 503, 504) or attempt == attempts - 1:
                return {'extraction': None, 'error': f'gemini {error.code}: {error.message}'[:300]}
            time.sleep(2 ** attempt * 3)
    meta = response.usage_metadata
    result = {'model': response.model_version or model, 'error': None, 'extraction': None,
              'usage': {'input': meta.prompt_token_count or 0,
                        'output': (meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0),
                        'cache_read': meta.cached_content_token_count or 0}}
    blocked = response.prompt_feedback and response.prompt_feedback.block_reason
    finish = response.candidates[0].finish_reason if response.candidates else None
    if blocked or (finish and finish.name in ('SAFETY', 'PROHIBITED_CONTENT', 'BLOCKLIST')):
        result['error'] = f'blocked: {blocked or finish.name}'
    elif response.parsed is None:
        result['error'] = f'no parsed output (finish_reason={finish.name if finish else None})'
    else:
        result['extraction'] = response.parsed.model_dump()
    return result


def _call_claude(client, post, model):
    import anthropic
    kwargs = {'betas': ['server-side-fallback-2026-07-01'], 'fallbacks': 'default'} if model in FALLBACK_MODELS else {}
    try:
        response = client.beta.messages.parse(
            model=model, max_tokens=2000,
            system=[{'type': 'text', 'text': SYSTEM, 'cache_control': {'type': 'ephemeral'}}],
            messages=[{'role': 'user', 'content': _user_message(post)}],
            output_config={'effort': 'low'}, output_format=PostExtraction, **kwargs)
    except (anthropic.BadRequestError, anthropic.NotFoundError, anthropic.AuthenticationError):
        raise
    except anthropic.APIError as error:
        return {'extraction': None, 'error': f'{type(error).__name__}: {error}'[:300]}
    result = {'model': response.model, 'error': None, 'extraction': None,
              'usage': {'input': response.usage.input_tokens, 'output': response.usage.output_tokens,
                        'cache_read': response.usage.cache_read_input_tokens or 0}}
    if response.stop_reason == 'refusal':
        result['error'] = 'refusal'
    elif response.parsed_output is None:
        result['error'] = f'no parsed output (stop_reason={response.stop_reason})'
    else:
        result['extraction'] = response.parsed_output.model_dump()
    return result


def extract_one(client, post, model):
    path = CACHE / model / PROMPT_VERSION / f"{post['id']}.json"
    if path.exists():
        return json.loads(path.read_text())
    started = time.time()
    call = _call_gemini if is_gemini(model) else _call_claude
    record = {'prompt_version': PROMPT_VERSION, **call(client, post, model), 'seconds': round(time.time() - started, 2)}
    if record['extraction'] is not None:  # Failures are not cached, so they retry on the next run.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record))
    return record


def to_tagged(post, record):
    row = fc.classify(post)
    found = record.get('extraction')
    if found is None:
        row.update(on_topic=False, historical=False, hotspots=[], actors=[], extraction_error=record.get('error'))
        row.update({signal: False for signal in fc.SIGNALS})
        return row
    types = set(found['event_types'])
    row.update(on_topic=found['relevant'], historical=found['historical'], hotspots=found['hotspots'], actors=found['actors'],
               event=bool(types), control_change='control_change' in types, escalation=found['escalation'],
               deescalation=found['deescalation'], fatalities=found['fatalities'], displaced=found['displaced'],
               other_places=found['other_places'], source_type=found['source_type'])
    return row


def tag_posts(posts, model, workers=4):
    client = _client(model)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        records = list(pool.map(lambda post: extract_one(client, post, model), posts))
    usage = {key: sum((r.get('usage') or {}).get(key, 0) for r in records) for key in ('input', 'output', 'cache_read')}
    errors = [r['error'] for r in records if r.get('error')]
    stats = {'model': model, 'prompt_version': PROMPT_VERSION, 'posts': len(posts), 'errors': len(errors),
             'error_examples': sorted({re.sub(r'\d+', '#', e) for e in errors})[:5], 'tokens': usage}
    return [to_tagged(post, record) for post, record in zip(posts, records)], stats
