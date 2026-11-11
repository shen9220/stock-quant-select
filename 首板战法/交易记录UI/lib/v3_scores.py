"""Recover recorded scores without reranking or changing a trade.

V1 result filenames describe the limit day; V2 A/B filenames describe the
original signal/entry day. D2 always uses that original signal day, never its
shifted entry day. Only an exact program/day/code/path match is accepted.
"""
from bisect import bisect_right
from pathlib import Path
import math
import re
import threading

NUMBER = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)'
_FILES = {
    'stock': r'\d{8}_recommendations\.md',
    'stock_updata': r'\d{8}_stock_updata_recommendations\.md',
    'slow': r'\d{8}_slow_recommendations\.md',
    'slow_v2': r'\d{8}_slow_v2\.md',
    'up': r'\d{8}_up_recommendations\.md',
    'stock_v2': r'\d{4}-\d{2}-\d{2}_recommendations\.(?:json|md)',
}
_CACHE = {}
_LOCK = threading.RLock()


def number(value):
    """Zero is a score. Empty strings, booleans and nonfinite values are not."""
    if isinstance(value, bool) or value is None:
        return None
    match = re.fullmatch(r'\s*(' + NUMBER + r')\s*(?:[（(][^\n]*[）)])?\s*', str(value))
    if not match:
        return None
    result = float(match[1])
    return result if math.isfinite(result) else None


def eligible(row):
    return bool(str(row.get('stock_code') or '').strip()) and not (
        row.get('is_board_placeholder') or row.get('is_rank_placeholder'))


def identity(row, program):
    day = str(row.get('signal_date') or row.get('date') or '')[:10]
    code = str(row.get('stock_code') or '').strip().zfill(6)
    return day, code, str(row.get('path') or '') if program == 'stock_v2' else ''


def _date(path):
    text = path.name.split('_', 1)[0]
    return text if '-' in text else f'{text[:4]}-{text[4:6]}-{text[6:8]}'


def _signal_day(path, text, program, calendar):
    if program == 'stock_v2':
        return _date(path)
    explicit = re.search(r'买入日\s*[（(]D1[）)]\s*[:：]\s*(\d{4}-\d{2}-\d{2})', text)
    if not explicit:
        explicit = re.search(r'D1\s*[（(](\d{4}-\d{2}-\d{2})[）)]\s*开盘', text)
    if explicit:
        return explicit[1]
    i = bisect_right(calendar, _date(path))
    return calendar[i] if i < len(calendar) else None


def _markdown(text, program):
    """Read each symbol's own block, including the unselected V1 remainder."""
    current, path = None, ''
    entries = []
    for line in text.splitlines():
        section = re.search(r'路径\s*([AB])\b', line)
        if section and program == 'stock_v2':
            path, current = section[1], None
        header = re.match(r'^\s*(?:排名\s*\d+\s*[:：]\s*|(?:连板剔除|未核实)\s+)?(\d{6})\b(.*)', line)
        if header:
            current = dict(code=header[1], path=path)
            entries.append(current)
        elif re.match(r'^\s*(?:===|——|#{1,3}\s)', line):
            current = None
        if current is None or 'score' in current:
            continue
        score = re.search(r'(?:综合得分|得分)\s*[:：]?\s*(' + NUMBER + r')\s*([（(][^\n）)]*[）)])?', line)
        if score:
            current['score'] = number(score[1])
            if program == 'stock_v2' and score[2]:
                current['score_label'] = score[1] + score[2]
    return [e for e in entries if e.get('score') is not None]


class ResultScores:
    def __init__(self, program, directory, calendar):
        self.program, self.directory = program, Path(directory)
        files = sorted(p for p in self.directory.glob('*') if re.fullmatch(_FILES[program], p.name))
        self.signature = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in files)
        cache_key = program, str(self.directory), tuple(calendar)
        with _LOCK:
            cached = _CACHE.get(cache_key)
            if cached and cached[0] == self.signature:
                self.entries = cached[1]
                return
            entries = {}
            # Markdown fallback first; structured V2 totals take precedence.
            for file in sorted(files, key=lambda p: p.suffix == '.json'):
                try:
                    text = file.read_text(encoding='utf-8')
                    day = _signal_day(file, text, program, calendar)
                    if not day:
                        continue
                    if file.suffix == '.json':
                        import json
                        data = json.loads(text)
                        if not isinstance(data, dict):
                            continue
                        records = []
                        for item in data.get('recommendations', []):
                            if not isinstance(item, dict):
                                continue
                            if (item.get('trade_date') or data.get('d1') or day) != day:
                                continue
                            score = number(item.get('score_total'))
                            if score is None or item.get('path') not in ('A', 'B'):
                                continue
                            record = dict(code=str(item.get('code') or '').zfill(6), path=item['path'], score=score)
                            if isinstance(item.get('above_threshold'), bool):
                                record['score_label'] = str(score) + ('（过门槛）' if item['above_threshold'] else '（未过门槛）')
                            records.append(record)
                    else:
                        records = _markdown(text, program)
                    for item in records:
                        if program == 'stock_v2' and item['path'] not in ('A', 'B'):
                            continue
                        key = day, item['code'], item['path']
                        previous = entries.get(key, {})
                        entries[key] = dict(previous, **item, score_source='result', score_source_file=file.name)
                except (OSError, ValueError, TypeError):
                    # A partial result write cannot leak another day's score.
                    continue
            self.entries = entries
            _CACHE[cache_key] = self.signature, entries

    def fields(self, row):
        if not eligible(row):
            return {}
        score = number(row.get('score'))
        if score is not None:
            return {'score': score}
        score = number(row.get('score_label'))
        if score is not None:
            return {'score': score, 'score_source': 'recorded_label'}
        found = self.entries.get(identity(row, self.program))
        if found:
            return {k: v for k, v in found.items() if k in ('score', 'score_label', 'score_source', 'score_source_file')}
        # The score was already recorded on this exact trade by historical
        # backfill jobs. Do not recalculate it or borrow another symbol's note.
        note = re.search(r'\bscore\s*=\s*(' + NUMBER + r')(?=$|[\s;,；])', str(row.get('notes') or ''))
        if note:
            return {'score': number(note[1]), 'score_source': 'recorded_notes'}
        return {}

    def enrich(self, rows):
        return [dict(row, **self.fields(row)) for row in rows]


def attach_scores(replay, sources, index):
    """Also resolve the final symbol after a suspended-entry substitution."""
    exact = {identity(row, index.program): row for row in sources if eligible(row)}
    for row in replay['rows']:
        if not eligible(row):
            row['score'], row['score_label'] = None, None
            continue
        if row.get('substituted_from'):
            source = exact.get(identity(row, index.program), {})
            row['score'], row['score_label'] = source.get('score'), source.get('score_label')
            # Only this final symbol's notes may supply a recorded score.
            lookup = dict(row, notes=source.get('notes'))
        else:
            lookup = row
        row.update(index.fields(lookup))
    return replay
