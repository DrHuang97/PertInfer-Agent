"""Open mechanism-reconstruction tools, offline by default.

No task answer, benchmark package, prediction, scoring or reference loader is
imported here. MaterialSearch is the only corpus entry point. Local assets must
be explicitly listed by that entry point and pass path, size and SHA256 checks.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any
import xml.etree.ElementTree as ET

TOOL_VERSION = 'pertinfer-agent-v0.1.0+e2e-v1.14.1'
MAX_TEXT = 30000
MAX_JSON_ROWS = 100
CATALOG = {
    'catalog': 'List contracts, tool/code hashes and offline policy.',
    'search_materials': '(graph_path, process, keywords, limit=5, offset=0, filters=null): graph/process-grounded local search; filters source/source_kind/material_id; explicit pagination; no_match means unknown.',
    'read_material': '(material_id, section=null, offset=0, max_chars=12000): complete metadata/artifact list and explicit text pagination.',
    'read_text': '(material_id, artifact=null, section=null, offset=0, max_chars=12000): HTML/XML/PDF/plain text with provenance and pagination; artifact is listed path, sha256 or index.',
    'read_xml': '(material_id, artifact=null, section=null, offset=0, max_chars=12000): XML section id/title or full textual content; paginated.',
    'read_json': '(material_id, artifact=null, pointer="", offset=0, limit=10, filters={}): original JSON; JSON pointer then exact dotted-field filters BEFORE pagination.',
    'molecular_identity': '(material_id, gene, taxon, artifact=null): exact primary UniProt gene and protein taxon, all matching rows; no orthology inference.',
    'orthology_records': '(material_id, source_taxon, target_taxon, source_gene_id=null, artifact=null): query orthology from either endpoint; original records/labels retained, query_matches explains original/reverse matching; MGI HTML read as text.',
    'drug_rows': '(material_id, name=null, molecule_id=null, inchi_key=null, artifact=null): exact ChEMBL molecule rows scanned in full before filtering; preserves chemical derivatives.',
    'read_pdf': '(material_id, artifact=null, page_start=1, page_end=null, offset=0, max_chars=12000): original PDF text, explicit 1-based page range and text pagination.',
    'render_pdf_page': '(material_id, page, artifact=null, max_dimension=2200): render one verified PDF page to workspace; return absolute PNG path for view_image.',
    'read_image': '(material_id, artifact=null): verified corpus image path/sha for view_image; no invented image interpretation.',
    'save_graph': '(graph, reason, stage="hypothesis"): immutable free graph version in workspace; not a D0 selector.',
    'validate_graph': '(graph_path, require_evidence=false): IDs/relations/endpoints and optional ledger references, no correctness/gold scoring.',
    'graph_history': 'List workspace graph versions/reasons/hashes.',
    'add_evidence': '(entry): append immutable evidence record with evidence_id, claim, support_class, material_ids, locator, context; optional anchor_text verified literally.',
    'write_ledger': 'Export current evidence JSON/CSV; returns local paths.',
    'manifest': 'Hash workspace artifacts, corpus and toolkit; omit its own recursive hash. Tokens/cost missing unless actually metered.',
    'archive_url': '(url,max_bytes=60000000): online-only HTTPS archive, cap+1 completeness check and original/partial hash; offline explicitly rejects.',
    'online_research': '(tool, arguments): explicit online-only existing ResearchToolsV3 adapter; offline rejects.',
}


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _digest(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)


def _rows(path):
    p = Path(path)
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


def _integer(value, low, high, label):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f'{label} must be integer {low}..{high}')
    return value


def _page(text, args):
    offset = _integer(args.get('offset', 0), 0, len(text), 'offset')
    count = _integer(args.get('max_chars', 12000), 1, MAX_TEXT, 'max_chars')
    end = min(len(text), offset + count)
    return dict(text=text[offset:end], offset=offset, end_offset=end,
                total_chars=len(text), next_offset=end if end < len(text) else None,
                has_more=end < len(text), truncated=end < len(text))


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'): self.hidden += 1
        if tag in ('p', 'div', 'tr', 'li', 'h1', 'h2', 'h3', 'br'): self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'): self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)


def _html_text(value):
    parser = _HTMLText(); parser.feed(value)
    return '\n'.join(' '.join(x.split()) for x in ''.join(parser.parts).splitlines() if x.strip())


def _field(row, key):
    value = row
    for p in key.split('.'):
        if not isinstance(value, dict) or p not in value: return None
        value = value[p]
    return value


def _xml_tag(element):
    return element.tag.rsplit('}', 1)[-1]

def _xml_tables(root):
    tables = []
    for table in root.iter():
        if _xml_tag(table) != 'table': continue
        parents = {child: parent for parent in table.iter() for child in parent}
        rows = []
        for row in table.iter():
            if _xml_tag(row) != 'tr': continue
            cells = [dict(text=' '.join(''.join(cell.itertext()).split()), tag=_xml_tag(cell),
                          colspan=cell.get('colspan', '1'), rowspan=cell.get('rowspan', '1'))
                     for cell in row if _xml_tag(cell) in ('th', 'td')]
            rows.append(dict(section=_xml_tag(parents[row]) if row in parents else None, cells=cells))
        tables.append(dict(table_id=table.get('id') or root.get('id'), rows=rows,
                           limitation='Cell boundaries and span attributes preserved; merged-cell expansion and scientific interpretation not inferred.'))
    return tables

def _xml_structured_text(root):
    blocks = {'p','sec','section','title','label','caption','list-item','table-wrap','abstract','ref'}
    def visit(element):
        tag = _xml_tag(element)
        if tag == 'table':
            tables = _xml_tables(element)
            return '\n' + '\n'.join('\t'.join(c['text'] for c in row['cells']) for t in tables for row in t['rows']) + '\n'
        text = element.text or ''
        for child in element:
            text += visit(child) + (child.tail or '')
        return ('\n' + text + '\n') if tag in blocks else text
    return visit(root).strip()

class E2ETools:
    def __init__(self, corpus_root, workspace, *, online=False, material_search=None):
        self.corpus_root = Path(corpus_root).resolve()
        self.workspace = Path(workspace).resolve()
        if self.workspace == self.corpus_root or self.corpus_root in self.workspace.parents:
            raise ValueError('workspace must be separate from corpus_root')
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.audit = self.workspace / 'tool_audit'; self.audit.mkdir(exist_ok=True)
        self.online = bool(online)
        self._material_search = material_search
        self._online_tools = None

    @property
    def materials(self):
        if self._material_search is None:
            from .material_search import MaterialSearch
            self._material_search = MaterialSearch(self.corpus_root)
        return self._material_search

    @contextlib.contextmanager
    def _lock(self):
        with (self.audit / 'lock').open('a') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield
            fcntl.flock(f, fcntl.LOCK_UN)

    def _workspace_path(self, value, *, exists=True):
        if not isinstance(value, (str, os.PathLike)): raise ValueError('workspace path required')
        p = Path(value)
        p = (self.workspace / p).resolve() if not p.is_absolute() else p.resolve()
        if not p.is_relative_to(self.workspace): raise PermissionError('path must belong to this workspace')
        if exists and not p.is_file(): raise FileNotFoundError('workspace file missing')
        return p

    def _graph(self, value):
        p = self._workspace_path(value)
        g = json.loads(p.read_text())
        self._validate_graph_data(g)
        return g

    def _material(self, mid, section=None):
        if not isinstance(mid, str) or not mid: raise ValueError('material_id required')
        out = self.materials.read_material(mid, section=section)
        if not isinstance(out, dict): raise ValueError('read_material did not return object')
        if out.get('status') in ('error', 'not_found', 'invalid_query'):
            raise ValueError('material unavailable: ' + str(out.get('error', out.get('status'))))
        if out.get('material_id') != mid: raise ValueError('material identity mismatch')
        return out

    def _asset(self, args, mime_prefix=None):
        material = self._material(args['material_id'])
        assets = material.get('artifacts', [])
        selector = args.get('artifact')
        if selector is None:
            selected = [x for x in assets if not mime_prefix or str(x.get('mime_type', '')).startswith(mime_prefix)]
            if len(selected) != 1: raise ValueError('artifact is absent or ambiguous; specify listed path, sha256 or 0-based index')
            a = selected[0]
        elif isinstance(selector, int) and not isinstance(selector, bool):
            if not 0 <= selector < len(assets): raise ValueError('artifact index out of range')
            a = assets[selector]
        else:
            selected = [x for x in assets if selector in (x.get('path'), x.get('sha256'))]
            if len(selected) != 1: raise ValueError('artifact must match exactly one registered path or sha256')
            a = selected[0]
        p = Path(a['path'])
        p = (self.corpus_root / p).resolve() if not p.is_absolute() else p.resolve()
        if not p.is_relative_to(self.corpus_root): raise PermissionError('corpus artifact resolves outside corpus_root')
        if not p.is_file(): raise FileNotFoundError('registered corpus artifact missing')
        if not re.fullmatch(r'[0-9a-f]{64}', str(a.get('sha256', ''))): raise ValueError('registered artifact needs SHA256')
        if _sha(p) != a['sha256']: raise ValueError('corpus artifact SHA256 mismatch')
        if a.get('bytes') != p.stat().st_size: raise ValueError('corpus artifact byte count mismatch')
        if mime_prefix and not str(a.get('mime_type', '')).startswith(mime_prefix): raise ValueError('artifact MIME mismatch')
        return p, dict(a, path=str(p)), material

    @staticmethod
    def _provenance(material, asset=None):
        return dict(material_id=material['material_id'], title=material.get('title'),
                    source_url=material.get('source_url'), source_kind=material.get('source_kind'),
                    scope=material.get('scope'), artifact=asset, source_locator=material.get('source_locator'),
                    retrieval_scope=material.get('retrieval_scope'),
                    caution='Original material; identity, host/protein taxa, construct, dose/time, measurement and causal strength require assessment.')

    @staticmethod
    def _validate_graph_data(g):
        if not isinstance(g, dict) or not isinstance(g.get('nodes'), list): raise ValueError('graph object with nodes list required')
        ids = [x.get('id') for x in g['nodes'] if isinstance(x, dict)]
        if len(ids) != len(g['nodes']) or any(not isinstance(x, str) or not x for x in ids): raise ValueError('every node needs nonempty string id')
        if len(set(ids)) != len(ids): raise ValueError('duplicate node id')
        reactions = g.get('reactions', [])
        if not isinstance(reactions, list): raise ValueError('reactions must be list')
        reaction_ids = [r.get('id') for r in reactions if isinstance(r, dict)]
        if len(reaction_ids) != len(reactions) or any(not isinstance(x, str) or not x for x in reaction_ids): raise ValueError('reaction id required')
        if len(set(reaction_ids)) != len(reaction_ids): raise ValueError('duplicate reaction id')
        if set(ids) & set(reaction_ids): raise ValueError('node and reaction ids must be distinct')
        for reaction in reactions:
            for key in ('inputs', 'outputs'):
                values = reaction.get(key, [])
                if not isinstance(values, list) or any(x not in ids for x in values): raise ValueError('reaction node missing')
        rs = g.get('relations', [])
        if not isinstance(rs, list): raise ValueError('relations must be list')
        rids = []
        for r in rs:
            if not isinstance(r, dict) or not isinstance(r.get('id'), str) or not r['id']: raise ValueError('every relation needs nonempty string id')
            allowed = ids + reaction_ids if r.get('kind') == 'dependency' else ids
            if r.get('source') not in allowed or r.get('target') not in allowed: raise ValueError('relation endpoint is not a graph node (reaction endpoints require kind=dependency)')
            rids.append(r['id'])
        if len(set(rids)) != len(rids): raise ValueError('duplicate relation id')
        for key in ('start', 'endpoint'):
            if key in g and g[key] not in ids: raise ValueError(key + ' is not a graph node')
        reactions = g.get('reactions', [])
        if not isinstance(reactions, list): raise ValueError('reactions must be list')
        reaction_ids = []
        for r in reactions:
            if not isinstance(r, dict) or not isinstance(r.get('id'), str): raise ValueError('reaction id required')
            reaction_ids.append(r['id'])
            for key in ('inputs', 'outputs'):
                if any(x not in ids for x in r.get(key, [])): raise ValueError('reaction node missing')
        if len(set(reaction_ids)) != len(reaction_ids): raise ValueError('duplicate reaction id')
        return dict(node_count=len(ids), relation_count=len(rs), reaction_count=len(reactions),
                    biological_correctness='not evaluated; no gold/reference access', complete_necessary_members=g.get('necessary_members_complete', 'unknown'))

    def call(self, name, args=None):
        start = _now(); t = time.monotonic()
        try:
            if isinstance(args, str): args = json.loads(args)
            if args is None: args = {}
            if not isinstance(args, dict): raise ValueError('arguments must be object')
            if name not in CATALOG: raise ValueError('unknown tool; use catalog')
            with self._lock():
                result = self._dispatch(name, args)
            status = result.get('status', 'ok') if isinstance(result, dict) else 'ok'
        except Exception as exc:
            result = dict(status='error', error_type=type(exc).__name__, error=str(exc), biological_verdict='unknown')
            status = 'error'
        with self._lock():
            log = self.audit / 'events.jsonl'
            prior = _rows(log)
            seq = len(prior) + 1
            event = dict(sequence=seq, tool_version=TOOL_VERSION, tool=name, arguments=args,
                         started_utc=start, elapsed_seconds=time.monotonic() - t, status=status,
                         result=result, token_usage='missing', cost='missing', online_enabled=self.online,
                         previous_event_sha256=prior[-1]['event_sha256'] if prior else None)
            event['event_sha256'] = _digest(event)
            with log.open('a') as f: f.write(json.dumps(event, ensure_ascii=False) + '\n')
            _write(self.audit / f'event_{seq:05d}.json', event)
        return dict(result, audit=dict(sequence=seq, event_sha256=event['event_sha256'],
                                      event_path=str(self.audit / f'event_{seq:05d}.json')))

    def _dispatch(self, name, a):
        if name == 'catalog':
            return dict(status='ok', version=TOOL_VERSION, tools=CATALOG, online_enabled=self.online,
                        corpus_root=str(self.corpus_root), workspace=str(self.workspace),
                        toolkit=self._tool_hashes(), pagination='Never interpret omitted/unread text as negative evidence.',
                        policy='Offline default. Corpus-only read, no gold, no old answers, no D0 graph restriction.')
        if name == 'search_materials':
            kwargs = {'limit': a.get('limit', 5)}
            for key in ('offset', 'filters'):
                if key in a: kwargs[key] = a[key]
            out = self.materials.search(self._graph(a['graph_path']), a['process'], a['keywords'], **kwargs)
            if not isinstance(out, dict): raise ValueError('material search result must be object')
            return dict(out, biological_verdict='unknown if no_match; search validation is not biological truth')
        if name == 'read_material':
            m = self._material(a['material_id'], a.get('section'))
            # The parent entrypoint text is complete; archive it before pagination.
            dest = self.audit / 'material_reads' / (_digest(m) + '.json'); _write(dest, m)
            return dict(status='ok', **{k:v for k,v in m.items() if k not in ('text','status')},
                        **_page(str(m.get('text', '')), a), full_response_archive=str(dest), full_response_sha256=_sha(dest))
        if name in ('read_text', 'read_xml'):
            if name == 'read_xml' and a.get('section') is not None and a.get('artifact') is None:
                material = self._material(a['material_id'])
                selected = [sec for sec in material.get('sections', []) if sec.get('id') == a['section']]
                if len(selected) > 1: raise ValueError('Corpus section_id is ambiguous; source index requires disambiguation')
                if selected:
                    # Prefer exact raw table structure when the same registered section ID exists.
                    xml_assets = [x for x in material.get('artifacts', []) if 'xml' in x.get('mime_type','')]
                    if len(xml_assets) == 1:
                        rawroot = ET.parse(xml_assets[0]['path']).getroot()
                        matches = [x for x in rawroot.iter() if x.get('id') == a['section']]
                        if len(matches) == 1 and _xml_tables(matches[0]):
                            selected_raw = matches[0]
                            return dict(status='ok', **_page(_xml_structured_text(selected_raw), a),
                                        tables=_xml_tables(selected_raw), table_cell_boundaries_preserved=True,
                                        section=a['section'], reading_path='registered_raw_xml_structured_table',
                                        provenance=self._provenance(material, xml_assets[0]))
                    material = self._material(a['material_id'], a['section'])
                    return dict(status='ok', **_page(str(material.get('text','')), a), provenance=self._provenance(material),
                                section=a['section'], reading_path='corpus_original_section_text', xml_element_resolution_used=False,
                                limitation='Corpus section_id may be synthetic BioC/index ID; original extracted section text+locator, not a claim that this ID exists in raw XML.')
            if name == 'read_text' and a.get('artifact') is None:
                m = self._material(a['material_id'], a.get('section'))
                return dict(status='ok', **_page(str(m.get('text', '')), a), provenance=self._provenance(m))
            p, asset, m = self._asset(a)
            mime = asset.get('mime_type', '')
            if 'pdf' in mime: return self._pdf_text(a, p, asset, m)
            raw = p.read_text(encoding='utf-8', errors='replace')
            if name == 'read_xml' or 'xml' in mime:
                root = ET.fromstring(raw); sec = a.get('section')
                if sec:
                    matches = [x for x in root.iter() if x.get('id') == sec or
                               (x.tag in ('sec', 'section') and ''.join(x.findtext('title', '')).casefold() == str(sec).casefold())]
                    if len(matches) != 1: raise ValueError('XML section id/title absent or ambiguous')
                    root = matches[0]
                text = _xml_structured_text(root)
            elif 'html' in mime: text = _html_text(raw)
            else: text = raw
            return dict(status='ok', **_page(text, a), provenance=self._provenance(m, asset), section=a.get('section'),
                        **(dict(tables=_xml_tables(root), table_cell_boundaries_preserved=True) if name == 'read_xml' or 'xml' in mime else {}))
        if name in ('read_json', 'molecular_identity', 'orthology_records', 'drug_rows'):
            p, asset, m = self._asset(a, 'application/json')
            data = json.loads(p.read_text())
            if name == 'read_json':
                pointer = a.get('pointer', '')
                if pointer and not pointer.startswith('/'): raise ValueError('JSON pointer must begin with /')
                for bit in pointer.split('/')[1:]:
                    bit = bit.replace('~1', '/').replace('~0', '~')
                    data = data[int(bit)] if isinstance(data, list) else data[bit]
                filters = a.get('filters', {})
                if not isinstance(filters, dict): raise ValueError('filters must be object of exact dotted-field matches')
                if isinstance(data, list):
                    alln = len(data); data = [x for x in data if all(_field(x,k)==v for k,v in filters.items())]
                    offset = _integer(a.get('offset',0),0,len(data),'offset');limit=_integer(a.get('limit',10),1,MAX_JSON_ROWS,'limit');end=min(len(data),offset+limit)
                    return dict(status='ok', rows=data[offset:end], rows_scanned=alln, total_matching_rows=len(data),
                                offset=offset,next_offset=end if end<len(data) else None,has_more=end<len(data),provenance=self._provenance(m,asset))
                if filters: raise ValueError('row filters require list at JSON pointer')
                # Object can contain long text/lists: serialize with explicit text pagination.
                text=json.dumps(data,ensure_ascii=False,indent=2)
                return dict(status='ok', json_type=type(data).__name__, **_page(text,a),provenance=self._provenance(m,asset))
            if name == 'molecular_identity':
                taxon=_integer(a['taxon'],1,999999999,'taxon');gene=a['gene']
                rows=data.get('results',[data]) if isinstance(data,dict) else data
                rows=[r for r in rows if isinstance(r,dict)]
                matches=[r for r in rows if r.get('organism',{}).get('taxonId')==taxon and any(g.get('geneName',{}).get('value','').casefold()==str(gene).casefold() for g in r.get('genes',[]))]
                parsed=[]
                for r in matches:
                    xref=r.get('uniProtKBCrossReferences',[])
                    parsed.append(dict(accession=r.get('primaryAccession'),entry_type=r.get('entryType'),organism=r.get('organism'),genes=r.get('genes'),protein_description=r.get('proteinDescription'),
                                       ensembl_gene_ids=sorted({p['value'] for x in xref if x.get('database')=='Ensembl' for p in x.get('properties',[]) if p.get('key')=='GeneId'}),
                                       cross_reference_identity=[x for x in xref if x.get('database') in ('HGNC','MGI','GeneID')]))
                return dict(status='ok' if matches else 'no_match',identities=parsed,rows_scanned=len(rows),matched_rows=len(matches),provenance=self._provenance(m,asset),limitation='Protein taxon, not assay host; aliases and orthology do not establish causal transfer. No matches means unknown.')
            if name == 'orthology_records':
                st=_integer(a['source_taxon'],1,999999999,'source_taxon');tt=_integer(a['target_taxon'],1,999999999,'target_taxon')
                rows=[h for x in data.get('data',[]) for h in x.get('homologies',[])]
                matches=[];query_matches=[]
                for h in rows:
                    source=h.get('source',{});target=h.get('target',{})
                    direct=source.get('taxon_id')==st and target.get('taxon_id')==tt and (not a.get('source_gene_id') or source.get('id')==a['source_gene_id'])
                    reverse=str(h.get('type','')).startswith('ortholog_') and target.get('taxon_id')==st and source.get('taxon_id')==tt and (not a.get('source_gene_id') or target.get('id')==a['source_gene_id'])
                    if not direct and not reverse:continue
                    qs,qt=(source,target) if direct else (target,source)
                    query_matches.append(dict(record_index=len(matches),matched_orientation='original' if direct else 'reverse',
                        query_source=dict(id=qs.get('id'),taxon_id=qs.get('taxon_id')),query_target=dict(id=qt.get('id'),taxon_id=qt.get('taxon_id')),
                        original_record_preserved=True))
                    matches.append(h)
                return dict(status='ok' if matches else 'no_match',records=matches,query_matches=query_matches,rows_scanned=len(rows),matched_rows=len(matches),provenance=self._provenance(m,asset),
                    limitation='Orthology may be queried from either endpoint. Original source/target, directional identity metrics and type labels remain unchanged; reverse matching does not invert labels or imply causal transfer. Other relationship types are not automatically reversed.')
            if name == 'drug_rows':
                if not any(a.get(k) for k in ('name','molecule_id','inchi_key')):raise ValueError('At least one exact chemical identity filter required')
                rows=data.get('molecules',[data]) if isinstance(data,dict) else data
                matches=[r for r in rows if (not a.get('name') or str(a['name']).casefold() in [str(r.get('pref_name','')).casefold()] + [str(x.get(k,'')).casefold() for x in r.get('molecule_synonyms',[]) for k in ('molecule_synonym','synonyms')]) and
                         (not a.get('molecule_id') or r.get('molecule_chembl_id')==a['molecule_id']) and
                         (not a.get('inchi_key') or r.get('molecule_structures',{}).get('standard_inchi_key')==a['inchi_key'])]
                return dict(status='ok' if matches else 'no_match',rows=matches,rows_scanned=len(rows),matched_rows=len(matches),
                            provenance=self._provenance(m,asset),name_matching='Case-insensitive exact pref_name or original molecule_synonyms names; all matched rows preserved.', limitation='Name retrieval is not chemical equivalence, activity or task-cell mechanism; derivatives/salts/stereoisomers not normalized or merged. No matches means unknown.')
        if name in ('read_pdf','render_pdf_page'):
            p,asset,m=self._asset(a,'application/pdf')
            if name=='read_pdf': return self._pdf_text(a,p,asset,m)
            page=_integer(a['page'],1,self._pdf_pages(p),'page');dim=_integer(a.get('max_dimension',2200),100,4000,'max_dimension')
            binary=shutil.which('pdftoppm')
            if not binary:raise RuntimeError('not_configured: pdftoppm unavailable')
            dest=self.workspace/'derived'/f"{asset['sha256'][:16]}_p{page}_{dim}"
            dest.parent.mkdir(exist_ok=True)
            proc=subprocess.run([binary,'-f',str(page),'-l',str(page),'-scale-to',str(dim),'-png','-singlefile',str(p),str(dest)],capture_output=True,text=True,timeout=60)
            if proc.returncode:raise RuntimeError('PDF rendering failed: '+proc.stderr[:1500])
            png=dest.with_suffix('.png')
            return dict(status='ok',path=str(png),sha256=_sha(png),page=page,provenance=self._provenance(m,asset),next_action='Use view_image on this absolute path. Rendering does not itself constitute visual reading.')
        if name=='read_image':
            p,asset,m=self._asset(a,'image/')
            return dict(status='ok',path=str(p),sha256=asset['sha256'],provenance=self._provenance(m,asset),next_action='Use view_image; text metadata does not substitute for pixels.')
        if name=='save_graph':
            graph=a['graph'];validation=self._validate_graph_data(graph)
            if not isinstance(a.get('reason'),str) or not a['reason'].strip():raise ValueError('nonempty change reason required')
            history=self.workspace/'graphs/history.jsonl';prior=_rows(history);version=len(prior)+1
            p=self.workspace/'graphs'/f'g{version:04d}_{_digest(graph)[:12]}.json'
            _write(p,graph)
            item=dict(version=version,path=str(p),sha256=_sha(p),reason=a['reason'],stage=a.get('stage','hypothesis'),created_utc=_now(),parent_sha256=prior[-1]['sha256'] if prior else None)
            with history.open('a') as f:f.write(json.dumps(item,ensure_ascii=False)+'\n')
            return dict(status='ok',**item,validation=validation)
        if name=='validate_graph':
            graph=self._graph(a['graph_path']);v=self._validate_graph_data(graph)
            if a.get('require_evidence'):
                known={x['evidence_id'] for x in _rows(self.workspace/'evidence/ledger.jsonl')}
                missing=sorted({z for r in graph.get('relations',[]) for z in r.get('evidence_ids',[]) if z not in known})
                unsupported=[r['id'] for r in graph.get('relations',[]) if not r.get('evidence_ids') and r.get('support_class') not in ('unknown','hypothesis')]
                if missing or unsupported:raise ValueError('unresolved evidence references '+str(missing)+' or unmarked unsupported relations '+str(unsupported))
                v['evidence_references_valid']=True
            return dict(status='ok',validation=v)
        if name=='graph_history':return dict(status='ok',versions=_rows(self.workspace/'graphs/history.jsonl'))
        if name=='add_evidence':
            e=a['entry']
            for k in ('evidence_id','claim','support_class','material_ids','locator','context'):
                if k not in e:raise ValueError('evidence entry missing '+k)
            if e['support_class'] not in ('direct_support','inference','unknown','database_annotation'):raise ValueError('invalid evidence support_class')
            if not isinstance(e['evidence_id'],str) or not e['evidence_id']:raise ValueError('evidence_id required')
            if not isinstance(e['material_ids'],list):raise ValueError('material_ids must be list')
            input_refs = e.get('input_refs', [])
            if not input_refs and not e['material_ids'] and e['support_class'] in ('inference','unknown'):
                input_refs = sorted(set(re.findall(r'(?<![A-Za-z0-9_/])input/(?:task|mouse_graph|mouse_provenance|source_graph|source_provenance)\.json', str(e.get('locator','')))))
            if not isinstance(input_refs,list):raise ValueError('input_refs must be list of supplied input paths')
            input_metadata = []
            for ref in input_refs:
                ip = self._workspace_path(ref)
                allowed_inputs = {self.workspace / x for x in ('input/task.json','input/mouse_graph.json','input/mouse_provenance.json','input/source_graph.json','input/source_provenance.json')}
                if ip not in allowed_inputs:raise ValueError('Only this workspace supplied task input_refs allowed')
                normalized_ref = str(ip.relative_to(self.workspace))
                if not ip.is_file():raise FileNotFoundError('Cited supplied input missing')
                raw_input = ip.read_text()
                archived = self.audit/'material_reads'/('supplied_input_'+_sha(ip)+'.json')
                archived.parent.mkdir(parents=True, exist_ok=True)
                archived.write_bytes(ip.read_bytes())
                input_metadata.append(dict(source_kind='supplied_task_input',path=normalized_ref,sha256=_sha(ip),archive=str(archived),archive_sha256=_sha(archived),independent_source_verification=False))
            if not e['material_ids'] and e['support_class'] != 'unknown' and not (e['support_class']=='inference' and input_metadata):raise ValueError('Corpus material_ids required; inference may instead cite explicit supplied input_refs, never independent direct support')
            if input_metadata and e['support_class'] not in ('inference','unknown'):raise ValueError('Supplied task inputs may only ground inference/unknown, not independent direct support')
            ledger=self.workspace/'evidence/ledger.jsonl';prior=_rows(ledger)
            if any(x['evidence_id']==e['evidence_id'] for x in prior):raise ValueError('evidence_id already exists; append a new revision id')
            mats=[self._material(x) for x in e['material_ids']]
            anchor=e.get('anchor_text');verified=None
            if anchor:
                if not isinstance(anchor,str):raise ValueError('anchor_text must be string')
                verified=any(anchor in str(x.get('text','')) for x in mats)
                if not verified:raise ValueError('anchor_text not present literally in cited material text; use correct material/section or image locator without fabricated quote')
            entry=dict(e,created_utc=_now(),anchor_verified=verified,source_metadata=[self._provenance(x) for x in mats]+input_metadata, input_refs=input_refs)
            ledger.parent.mkdir(exist_ok=True)
            with ledger.open('a') as f:f.write(json.dumps(entry,ensure_ascii=False)+'\n')
            return dict(status='ok',entry=entry,causal_truth='Not automatically validated; citation/anchor check only.')
        if name=='write_ledger':
            import csv
            entries=_rows(self.workspace/'evidence/ledger.jsonl');p=self.workspace/'output/evidence_ledger.json';_write(p,dict(evidence=entries))
            cp=p.with_suffix('.csv')
            with cp.open('w') as f:
                w=csv.DictWriter(f,fieldnames=['evidence_id','claim','support_class','material_ids','locator','context']);w.writeheader()
                for e in entries:w.writerow({k:json.dumps(e.get(k),ensure_ascii=False) if isinstance(e.get(k),(list,dict)) else e.get(k) for k in w.fieldnames})
            return dict(status='ok',json_path=str(p),csv_path=str(cp),entry_count=len(entries))
        if name=='manifest':return self._manifest()
        if name=='archive_url':return self._archive_url(a)
        if name=='online_research':
            if not self.online:raise PermissionError('offline policy: network research disabled')
            if self._online_tools is None:
                from .research_tools_v3 import ResearchToolsV3
                self._online_tools=ResearchToolsV3(self.workspace/'online_research')
            return dict(status='ok',result=self._online_tools.call(a['tool'],a.get('arguments',{})),tool_origin='existing_repository_ResearchToolsV3_optional_online')
        raise ValueError('unimplemented tool')

    def _pdf_pages(self,p):
        with Path(p).open('rb') as f:
            if b'%PDF-' not in f.read(1024): raise ValueError('PDF header missing')
            f.seek(max(0, Path(p).stat().st_size - 2048))
            if b'%%EOF' not in f.read(): raise ValueError('PDF EOF missing; incomplete asset is not evidence')
        binary=shutil.which('pdfinfo')
        if not binary:raise RuntimeError('not_configured: pdfinfo unavailable')
        proc=subprocess.run([binary,str(p)],capture_output=True,text=True,timeout=30)
        match=re.search(r'^Pages:\s+(\d+)',proc.stdout,re.M)
        if proc.returncode or not match:raise ValueError('PDF metadata invalid or incomplete: '+proc.stderr[:500])
        return int(match.group(1))

    def _pdf_text(self,a,p,asset,m):
        binary=shutil.which('pdftotext')
        if not binary:raise RuntimeError('not_configured: pdftotext unavailable')
        total=self._pdf_pages(p);start=_integer(a.get('page_start',1),1,total,'page_start');end=_integer(a.get('page_end') or total,start,total,'page_end')
        proc=subprocess.run([binary,'-f',str(start),'-l',str(end),'-layout',str(p),'-'],capture_output=True,text=True,timeout=60)
        if proc.returncode:raise ValueError('PDF text read failed: '+proc.stderr[:1200])
        fonts = shutil.which('pdffonts')
        font_check = dict(status='not_configured' if not fonts else 'unknown', fonts_without_unicode_map=[], original_report=None)
        if fonts:
            fp = subprocess.run([fonts, '-f', str(start), '-l', str(end), str(p)],capture_output=True,timeout=30)
            report = self.workspace/'derived'/('pdffonts_'+asset['sha256'][:16]+'_'+str(start)+'_'+str(end)+'.txt')
            report.parent.mkdir(parents=True,exist_ok=True);report.write_bytes(fp.stdout)
            font_stdout = fp.stdout.decode('utf-8', errors='replace')
            missing = [line.split()[0] for line in font_stdout.splitlines()[2:] if len(line.split()) >= 4 and line.split()[-3] == 'no']
            font_check = dict(status='checked' if fp.returncode == 0 else 'check_failed', fonts_without_unicode_map=missing,
                              original_report=str(report), original_report_sha256=_sha(report),
                              report_encoding='Raw diagnostic bytes preserved; parsed with UTF8 replacement only for font-name diagnostics')
        quality = dict(extraction_engine='pdftotext -layout', visual_verification_performed=False,
                       unit_and_symbol_fidelity='unverified', numerical_units_require_page_image_verification=True,
                       font_unicode_mapping=font_check, text_corrected_or_unit_substituted=False,
                       glyph_loss_risk='font_without_unicode_mapping' if font_check['fonts_without_unicode_map'] else 'not_excluded')
        return dict(status='ok',**_page(proc.stdout,a),page_start=start,page_end=end,total_pages=total,
                    provenance=self._provenance(m,asset),text_quality=quality,
                    limitation='PDF extraction can misdecode units/symbols (including micro versus milli) and table labels. Text is not verified quantitative evidence; render and actually view relevant pages. No automatic unit correction.')

    def _tool_hashes(self):
        root = Path(__file__).resolve().parent
        paths = sorted(root.glob('*.py')) + sorted(root.glob('*.md'))
        return [{'path': str(p), 'sha256': _sha(p)} for p in paths]

    def _manifest(self):
        dest=self.workspace/'output/manifest.json';files=[]
        audit_log=self.audit/'events.jsonl'
        audit_bytes=audit_log.read_bytes() if audit_log.exists() else b''
        snap=self.audit/'snapshots'/('events_'+hashlib.sha256(audit_bytes).hexdigest()+'.jsonl')
        snap.parent.mkdir(exist_ok=True)
        if not snap.exists(): snap.write_bytes(audit_bytes)
        covered=len(audit_bytes.splitlines())
        # Audit files are append-only; freeze the mutable JSONL before this call's receipt.
        for p in sorted(self.workspace.rglob('*')):
            if p.is_symlink():continue
            if p.is_file() and p not in (dest,audit_log) and p.name!='lock':
                files.append(dict(path=str(p.relative_to(self.workspace)),bytes=p.stat().st_size,sha256=_sha(p)))
        corpus=self.corpus_root/'corpus.json'
        obj=dict(schema='e2e_manifest_v1',created_utc=_now(),tool_version=TOOL_VERSION,toolkit=self._tool_hashes(),
                 corpus_sha256=_sha(corpus) if corpus.is_file() else 'missing',files=files,online_enabled=self.online,
                 graph_versions=_rows(self.workspace/'graphs/history.jsonl'),usage=dict(tokens='missing',cost='missing'),
                 audit_log_snapshot=dict(path=str(snap.relative_to(self.workspace)),sha256=_sha(snap),covered_sequences=covered,
                                         mutable_source=str(audit_log.relative_to(self.workspace))),
                 audit_note='Immutable audit snapshot before this manifest call receipt; mutable event JSONL and self omitted from file hashes. Final outer experiment seal must include the completed log and this receipt. No biological/reference score.')
        _write(dest,obj)
        return dict(status='ok',manifest_path=str(dest),sha256=_sha(dest),file_count=len(files))

    def _archive_url(self,a):
        if not self.online:raise PermissionError('offline policy: HTTPS archival disabled')
        from urllib.parse import urlsplit
        from urllib.request import Request,urlopen
        url=a['url'];parts=urlsplit(url)
        if parts.scheme!='https' or not parts.hostname or parts.username or parts.password:raise ValueError('public HTTPS URL without credentials required')
        cap=_integer(a.get('max_bytes',60000000),1,60000000,'max_bytes')
        receipt=dict(url=url,started_utc=_now(),complete=False)
        raw=b''
        try:
            with urlopen(Request(url,headers={'User-Agent':'PertAgent-E2ETools/1'}),timeout=45) as h:
                raw=h.read(cap+1);length=h.headers.get('Content-Length')
                if urlsplit(h.url).scheme != 'https': raise PermissionError('HTTPS downgrade redirect rejected')
                receipt.update(http_status=h.status,final_url=h.url,content_type=h.headers.get('Content-Type'),content_length_header=length,
                               complete=len(raw)<=cap and (length is None or int(length)==len(raw)))
            receipt.update(status='ok' if receipt['complete'] else 'incomplete',bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
        except Exception as exc:
            receipt.update(status='error',error_type=type(exc).__name__,error=str(exc),http_status=getattr(exc,'code',None))
        if raw:
            p=self.workspace/'downloads'/(hashlib.sha256(raw).hexdigest()+('.raw' if receipt['complete'] else '.partial'))
            p.parent.mkdir(exist_ok=True)
            if not p.exists():p.write_bytes(raw)
            receipt['path']=str(p)
        p=self.workspace/'downloads'/('receipt_'+_digest(receipt)+'.json');_write(p,receipt)
        return dict(receipt,receipt_path=str(p),biological_verdict='unknown; download integrity is not biological evidence')
