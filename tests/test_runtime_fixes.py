import os,sys,json,hashlib
from pathlib import Path
import pytest
from pertinfer_agent.e2e_tools import E2ETools
from pertinfer_agent.material_search import MaterialSearch
@pytest.fixture
def env(tmp_path):
 c=tmp_path/'corpus';c.mkdir();w=tmp_path/'workspace';w.mkdir();(w/'input').mkdir();(w/'input/source_graph.json').write_bytes(b'{"nodes":[]}\n')
 xml=b'<article><body><p id="raw">Original text <xref>2</xref> and tail.</p></body></article>';j=json.dumps({'molecules':[{'molecule_chembl_id':'MOCK1','pref_name':'REFERENCE','molecule_synonyms':[{'molecule_synonym':'AGENT'}]},{'molecule_chembl_id':'MOCK2','pref_name':'AGENT SALT','molecule_synonyms':[]}]}).encode();assets=[]
 for name,data,mime in [('x.xml',xml,'application/xml'),('x.json',j,'application/json')]:
  (c/name).write_bytes(data);assets.append({'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'mime_type':mime})
 materials=[{'material_id':'doc','title':'Mock original','source_url':'https://example.org/mock','source_kind':'original_article','text':'SF activates TARGET in HepG2. Agent was exposed. enzyme hydroxylation.','sections':[{'id':'synthetic-p-1','title':'Mock section','text':'SF activates TARGET in HepG2. Agent was exposed. enzyme hydroxylation.'}],'artifacts':assets}]
 (c/'corpus.json').write_text(json.dumps({'corpus_id':'synthetic_only','materials':materials,'entity_aliases':[]}));return c,w,E2ETools(c,w)
def test_namespace_literal_and_process_inflections(env):
 c,w,t=env;m=MaterialSearch(c)
 for prefix,name,word,kind in [('phenotype','platelet_aggregation','platelet aggregation','activation'),('protein','TARGET','TARGET','catalysis')]:
  q=[word,'hydroxylation' if kind=='catalysis' else 'activation'];assert m._validate({'nodes':[{'id':'x','entity':prefix+':'+name}]},{'node_ids':['x'],'kind':kind},q)
 assert m._validate({'nodes':[{'id':'x','entity':'compound:Agent'}]},{'node_ids':['x'],'kind':'perturbation'},['Agent','exposed'])
def test_strict_body_and_alias_explanation(env):
 c,w,t=env;m=MaterialSearch(c);g={'nodes':[{'id':'a','entity':'compound:long_agent','aliases':['SF']},{'id':'b','entity':'gene:TARGET'}]};p={'node_ids':['a','b'],'kind':'activation'}
 r=m.search(g,p,['long_agent','TARGET','activation'],filters={'text_all':['HepG2']});assert r['status']=='ok';h=r['results'][0];assert 'sf' in h['matched_molecular_terms'][0]['normalized_literal_matches'];assert h['alias_matching_is_literal_not_semantic'] is True
 assert m.search(g,p,['long_agent','TARGET','activation'],filters={'text_all':['missing_context']})['status']=='no_match'
def test_synthetic_section_and_root_text(env):
 c,w,t=env;r=t.call('read_xml',{'material_id':'doc','section':'synthetic-p-1'});assert r['status']=='ok' and r['reading_path']=='corpus_original_section_text' and 'HepG2' in r['text']
 r=t.call('read_xml',{'material_id':'doc','section':'raw','artifact':0});assert 'Original text 2 and tail.' in r['text']
def test_exact_original_synonym_preserves_forms(env):
 c,w,t=env;r=t.call('drug_rows',{'material_id':'doc','name':'agent'});assert r['status']=='ok' and len(r['rows'])==1 and r['rows'][0]['molecule_chembl_id']=='MOCK1'
def test_input_absolute_scope_raw_hash_and_no_direct_support(env,tmp_path):
 c,w,t=env;e={'evidence_id':'input_inference','claim':'Supplied input only, independently unverified.','support_class':'inference','material_ids':[],'locator':'input/source_graph.json','context':{},'input_refs':[str(w/'input/source_graph.json')]};r=t.call('add_evidence',{'entry':e});assert r['status']=='ok';meta=r['entry']['source_metadata'][0];assert meta['sha256']==meta['archive_sha256'];assert Path(meta['archive']).read_bytes()==(w/'input/source_graph.json').read_bytes()
 e=dict(e,evidence_id='direct',support_class='direct_support');assert t.call('add_evidence',{'entry':e})['status']=='error'
 outside=tmp_path/'outside.json';outside.write_text('{}');e=dict(e,evidence_id='outside',support_class='inference',input_refs=[str(outside)]);assert t.call('add_evidence',{'entry':e})['status']=='error'
def test_dependency_reaction_endpoint(env):
 g={'nodes':[{'id':'a'},{'id':'b'}],'reactions':[{'id':'r','inputs':['a'],'outputs':['b']}],'relations':[{'id':'dep','source':'a','target':'r','kind':'dependency'}]};assert E2ETools._validate_graph_data(g)['reaction_count']==1;g['relations'][0]['kind']='binding'
 with pytest.raises(ValueError):E2ETools._validate_graph_data(g)
