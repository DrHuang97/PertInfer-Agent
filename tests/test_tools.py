"""Synthetic interface/integrity tests. No benchmark/gold/old-run fixtures."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import pytest
from pertinfer_agent.e2e_tools import E2ETools


def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()


class Materials:
    def __init__(self, root): self.root=root;self.records={};self.searches=[]
    def add(self, mid, text='', data=None, mime='application/json'):
        artifacts=[]
        if data is not None:
            p=self.root/(mid+'.raw');p.write_bytes(data if isinstance(data,bytes) else json.dumps(data).encode())
            artifacts=[dict(path=str(p),mime_type=mime,sha256=digest(p),bytes=p.stat().st_size)]
        self.records[mid]=dict(material_id=mid,title='Original '+mid,source_url='https://example.org/'+mid,
                               text=text,section=None,artifacts=artifacts,source_kind='synthetic_test',scope={'host_taxon':'unknown'})
        return self.records[mid]
    def read_material(self, mid, section=None):
        if mid not in self.records:return {'status':'not_found','material_id':mid}
        out=copy.deepcopy(self.records[mid]);out['section']=section;return out
    def search(self, graph, process, keywords, limit=5):
        self.searches.append((graph,process,keywords,limit))
        return dict(status='no_match',results=[],corpus_id='synthetic',corpus_sha256='0'*64,query_validation={'valid':True})


@pytest.fixture
def env(tmp_path):
    c=tmp_path/'corpus';c.mkdir();m=Materials(c);t=E2ETools(c,tmp_path/'subject',material_search=m)
    return t,m


def graph():
    return dict(start='a',endpoint='b',nodes=[dict(id='a',gene='GENEA',taxon=9606),dict(id='b',gene='GENEB',taxon=9606)],
                relations=[dict(id='r1',source='a',target='b',support_class='hypothesis')])


def test_pagination_complete_tail_and_archive(env):
    t,m=env;m.add('m',text='A'*12000+'last paragraph')
    first=t.call('read_material',{'material_id':'m'})
    assert first['has_more'] and first['total_chars']==12014 and first['next_offset']==12000
    last=t.call('read_material',{'material_id':'m','offset':first['next_offset']})
    assert last['text']=='last paragraph' and last['next_offset'] is None
    full=json.loads(Path(first['full_response_archive']).read_text());assert full['text'].endswith('last paragraph')
    assert t.call('read_text',{'material_id':'m','offset':13000})['status']=='error'


def test_graph_source_must_belong_to_workspace_even_symlink(env,tmp_path):
    t,m=env;p=tmp_path/'external.json';p.write_text(json.dumps(graph()))
    req=dict(graph_path=str(p),process={'node_ids':['a'],'kind':'identity'},keywords=['GENEA','identity'])
    assert t.call('search_materials',req)['error_type']=='PermissionError'
    link=t.workspace/'link.json';link.symlink_to(p);req['graph_path']=str(link)
    assert t.call('search_materials',req)['error_type']=='PermissionError'
    assert not m.searches


def test_search_delegates_self_graph_and_does_not_classify_nomatch_false(env):
    t,m=env;out=t.call('save_graph',{'graph':graph(),'reason':'Initial hypothesis'})
    req=dict(graph_path=out['path'],process={'relation_id':'r1','kind':'activation'},keywords=['GENEA','GENEB','activation'],limit=3)
    result=t.call('search_materials',req)
    assert result['status']=='no_match' and 'unknown' in result['biological_verdict']
    assert m.searches[0]==(graph(),req['process'],req['keywords'],3)


def test_artifact_hash_and_size_must_match_before_read(env):
    t,m=env;rec=m.add('j',data={'x':1});p=Path(rec['artifacts'][0]['path']);p.write_text('{"x":2}')
    assert 'SHA256 mismatch' in t.call('read_json',{'material_id':'j'})['error']
    rec['artifacts'][0]['sha256']=digest(p);rec['artifacts'][0]['bytes']=100
    assert 'byte count mismatch' in t.call('read_json',{'material_id':'j'})['error']


def test_unregistered_and_outside_assets_rejected(env,tmp_path):
    t,m=env;rec=m.add('j',data={'x':1});outside=tmp_path/'outside';outside.write_text('secret')
    assert t.call('read_json',{'material_id':'j','artifact':str(outside)})['status']=='error'
    rec['artifacts'][0].update(path=str(outside),bytes=6,sha256=digest(outside))
    assert t.call('read_json',{'material_id':'j'})['error_type']=='PermissionError'


def test_json_fullscan_filters_before_pagination(env):
    t,m=env;m.add('j',data={'results':[{'gene':'NO','taxon':10090} for _ in range(80)]+[{'gene':'YES','taxon':9606}]})
    out=t.call('read_json',{'material_id':'j','pointer':'/results','filters':{'gene':'YES','taxon':9606},'limit':1})
    assert out['rows_scanned']==81 and out['total_matching_rows']==1 and out['rows']==[{'gene':'YES','taxon':9606}]


def test_exact_drug_row_preserves_derivatives(env):
    t,m=env;m.add('drug',data={'molecules':[{'pref_name':'AGENT ACETATE','molecule_chembl_id':'C1'}]*30+[{'pref_name':'AGENT','molecule_chembl_id':'C2','molecule_structures':{'standard_inchi_key':'KEY'}}]})
    out=t.call('drug_rows',{'material_id':'drug','name':'agent'})
    assert out['rows_scanned']==31 and out['matched_rows']==1 and out['rows'][0]['molecule_chembl_id']=='C2'
    assert t.call('drug_rows',{'material_id':'drug','name':'agent','inchi_key':'OTHER'})['status']=='no_match'
    assert t.call('drug_rows',{'material_id':'drug'})['status']=='error'


def test_species_and_primary_gene_identity_no_alias_substitution(env):
    t,m=env
    def protein(g,t,a):return {'primaryAccession':a,'organism':{'taxonId':t},'genes':[{'geneName':{'value':g}}],'entryType':'reviewed'}
    m.add('uni',data={'results':[protein('GENEA',10090,'MOUSE'),protein('GENEA',9606,'HUMAN'),protein('ALIAS',9606,'OTHER')]})
    out=t.call('molecular_identity',{'material_id':'uni','gene':'GENEA','taxon':9606})
    assert out['identities'][0]['accession']=='HUMAN' and out['matched_rows']==1
    assert t.call('molecular_identity',{'material_id':'uni','gene':'GENEA','taxon':1})['status']=='no_match'


def test_orthology_retains_source_label_and_pair_direction(env):
    t,m=env;row={'type':'ortholog_one2many','source':{'taxon_id':9606,'id':'H1','perc_id':91},'target':{'taxon_id':10090,'id':'M1','perc_id':87}}
    m.add('hom',data={'data':[{'homologies':[row]}]})
    out=t.call('orthology_records',{'material_id':'hom','source_taxon':9606,'target_taxon':10090})
    assert out['records']==[row] and out['records'][0]['type']=='ortholog_one2many'
    reverse=t.call('orthology_records',{'material_id':'hom','source_taxon':10090,'target_taxon':9606,'source_gene_id':'M1'})
    assert reverse['records']==[row]  # Raw endpoints, label and directional percentages stay intact.
    assert reverse['query_matches']==[{'record_index':0,'matched_orientation':'reverse','query_source':{'id':'M1','taxon_id':10090},'query_target':{'id':'H1','taxon_id':9606},'original_record_preserved':True}]
    assert t.call('orthology_records',{'material_id':'hom','source_taxon':10090,'target_taxon':9606,'source_gene_id':'H1'})['status']=='no_match'


def test_reverse_orthology_does_not_select_other_genes_or_reverse_nonorthology(env):
    t,m=env
    rows=[{'type':kind,'source':{'taxon_id':9606,'id':h},'target':{'taxon_id':10090,'id':mouse}} for kind,h,mouse in
          [('ortholog_one2one','H1','M1'),('ortholog_one2one','H2','M2'),('within_species_paralog','H3','M3')]]
    m.add('hom',data={'data':[{'homologies':rows}]})
    out=t.call('orthology_records',{'material_id':'hom','source_taxon':10090,'target_taxon':9606,'source_gene_id':'M2'})
    assert out['records']==[rows[1]]
    assert t.call('orthology_records',{'material_id':'hom','source_taxon':10090,'target_taxon':9606,'source_gene_id':'M3'})['status']=='no_match'


def test_xml_exact_section_and_pagination(env):
    t,m=env;m.add('xml',data=b'<article><body><sec id="s1"><title>Methods</title><p>RNA abundance measured.</p></sec><sec id="s2"><title>Results</title><p>Conditional result.</p></sec></body></article>',mime='application/xml')
    out=t.call('read_xml',{'material_id':'xml','section':'s2','max_chars':12})
    assert out['has_more'] and 'Methods' not in out['text']
    missing=t.call('read_xml',{'material_id':'xml','section':'unknown'})
    assert missing['status']=='error' and 'absent or ambiguous' in missing['error']


def test_free_graph_versions_and_evidence_validation(env):
    t,m=env;m.add('m',text='Native endogenous RNA increased.')
    g=graph();out=t.call('save_graph',{'graph':g,'reason':'Initial'})
    old=Path(out['path']).read_bytes();g['relations'][0].update(evidence_ids=['E1'],support_class='direct_support')
    new=t.call('save_graph',{'graph':g,'reason':'Source supports response','stage':'final'})
    assert new['version']==2 and Path(out['path']).read_bytes()==old
    assert t.call('validate_graph',{'graph_path':new['path'],'require_evidence':True})['status']=='error'
    entry=dict(evidence_id='E1',claim='Response increased',support_class='direct_support',material_ids=['m'],locator='Results paragraph',context={'measurement':'RNA'},anchor_text='RNA increased.')
    assert t.call('add_evidence',{'entry':entry})['entry']['anchor_verified']
    assert t.call('add_evidence',{'entry':entry})['status']=='error'
    assert t.call('validate_graph',{'graph_path':new['path'],'require_evidence':True})['status']=='ok'
    assert t.call('write_ledger',{})['entry_count']==1


def test_evidence_rejects_fabricated_quote_and_bad_graph(env):
    t,m=env;m.add('m',text='conditional')
    e=dict(evidence_id='E1',claim='x',support_class='inference',material_ids=['m'],locator='x',context={},anchor_text='made up')
    assert t.call('add_evidence',{'entry':e})['status']=='error'
    bad=graph();bad['relations'][0]['target']='missing'
    assert t.call('save_graph',{'graph':bad,'reason':'bad'})['status']=='error'


def test_offline_explicitly_rejects_and_records_online_requests(env):
    t,m=env
    for tool,a in [('archive_url',{'url':'https://example.org'}),('online_research',{'tool':'uniprot_search','arguments':{}})]:
        out=t.call(tool,a);assert out['status']=='error' and 'offline policy' in out['error']
    log=[json.loads(x) for x in (t.audit/'events.jsonl').read_text().splitlines()]
    assert len(log)==2 and all(x['status']=='error' and x['online_enabled'] is False for x in log)


def test_audit_chain_and_manifest_excludes_self(env):
    t,m=env;t.call('catalog',{});t.call('unknown',{});out=t.call('manifest',{})
    rows=[json.loads(x) for x in (t.audit/'events.jsonl').read_text().splitlines()]
    previous=None
    for x in rows:
        h=x.pop('event_sha256');assert x['previous_event_sha256']==previous
        assert hashlib.sha256(json.dumps(x,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()==h
        previous=h
    man=json.loads(Path(out['manifest_path']).read_text())
    assert all(x['path']!='output/manifest.json' for x in man['files']) and man['usage']['tokens']=='missing'


def minimal_pdf():
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    stream=b'BT /F1 12 Tf 20 250 Td (Native RNA result.) Tj ET'
    objects.append(b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream')
    data=b'%PDF-1.4\n';offsets=[0]
    for i,o in enumerate(objects,1):offsets.append(len(data));data+=str(i).encode()+b' 0 obj\n'+o+b'\nendobj\n'
    xref=len(data);data+=b'xref\n0 6\n0000000000 65535 f \n'
    for off in offsets[1:]:data+=f'{off:010d} 00000 n \n'.encode()
    return data+b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n'+str(xref).encode()+b'\n%%EOF\n'


@pytest.mark.skipif(not all(shutil.which(x) for x in ['pdfinfo','pdftotext','pdftoppm']),reason='Poppler not installed')
def test_real_pdf_text_render_and_page_bounds(env):
    t,m=env;m.add('pdf',data=minimal_pdf(),mime='application/pdf')
    out=t.call('read_pdf',{'material_id':'pdf'})
    assert out['status']=='ok' and 'Native RNA result.' in out['text'] and out['total_pages']==1
    assert t.call('render_pdf_page',{'material_id':'pdf','page':2})['status']=='error'
    rendered=t.call('render_pdf_page',{'material_id':'pdf','page':1,'max_dimension':400})
    assert Path(rendered['path']).read_bytes().startswith(b'\x89PNG')


def test_cli_transport_and_offline_default(tmp_path):
    root=Path(__file__).resolve().parents[1];c=tmp_path/'corpus';c.mkdir()
    req='{"name":"catalog"}\n{"name":"archive_url","arguments":{"url":"https://example.org"}}\n'
    proc=subprocess.run([sys.executable,'-m','pertinfer_agent.tool_cli','--corpus-root',str(c),'--workspace',str(tmp_path/'w')],input=req,capture_output=True,text=True)
    lines=[json.loads(x) for x in proc.stdout.splitlines()]
    assert proc.returncode==1 and lines[0]['online_enabled'] is False and lines[1]['error_type']=='PermissionError'


def test_online_archiver_keeps_partial_and_does_not_call_it_complete(env,monkeypatch):
    import urllib.request
    t,m=env;t.online=True
    class Response:
        headers={'Content-Length':'10','Content-Type':'application/pdf'};status=200;url='https://example.org/source'
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,n):return b'1234567890'[:n]
    monkeypatch.setattr(urllib.request,'urlopen',lambda *a,**k:Response())
    out=t.call('archive_url',{'url':'https://example.org/source','max_bytes':5})
    assert out['status']=='incomplete' and out['complete'] is False and out['bytes']==6
    assert Path(out['path']).suffix=='.partial' and Path(out['receipt_path']).is_file()
    full=t.call('archive_url',{'url':'https://example.org/source','max_bytes':20})
    assert full['complete'] is True and full['status']=='ok' and Path(out['path']).exists()


@pytest.mark.skipif(not shutil.which('pdfinfo'),reason='Poppler not installed')
def test_hash_matched_but_truncated_pdf_is_rejected(env):
    t,m=env;m.add('partial',data=minimal_pdf().split(b'startxref')[0],mime='application/pdf')
    out=t.call('read_pdf',{'material_id':'partial'})
    assert out['status']=='error' and 'EOF missing' in out['error']


def test_manifest_hashes_survive_own_audit_receipt_with_immutable_snapshot(env):
    t,m=env;t.call('catalog',{});result=t.call('manifest',{})
    obj=json.loads(Path(result['manifest_path']).read_text())
    for entry in obj['files']:
        assert digest(t.workspace/entry['path'])==entry['sha256']
    assert obj['audit_log_snapshot']['covered_sequences']==1
    assert len((t.audit/'events.jsonl').read_text().splitlines())==2
    snap=t.workspace/obj['audit_log_snapshot']['path']
    assert len(snap.read_text().splitlines())==1 and digest(snap)==obj['audit_log_snapshot']['sha256']


def test_material_filters_and_pagination_forwarded_without_bypassing_graph(env):
    t,m=env;saved=t.call('save_graph',{'graph':graph(),'reason':'query fixture'})
    received={}
    def search(g,process,keywords,limit=5,offset=0,filters=None):
        received.update(graph=g,process=process,keywords=keywords,limit=limit,offset=offset,filters=filters)
        return dict(status='ok',results=[{'material_id':'matching'}],offset=offset,next_offset=offset+1,has_more=True,total_matches=9)
    m.search=search
    args=dict(graph_path=saved['path'],process={'node_ids':['a'],'kind':'identity'},keywords=['GENEA','chemical'],limit=1,offset=3,filters={'source':'Public DB','source_kind':'json'})
    out=t.call('search_materials',args)
    assert received['offset']==3 and received['filters']==args['filters'] and received['graph']==graph()
    assert out['next_offset']==4 and out['has_more'] is True
