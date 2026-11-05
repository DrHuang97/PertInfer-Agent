"""Small, on-demand public evidence queries. No model weights or GPU work.

Open additional-data development only. Raw bytes are committed before packets
are exposed; failures and empty searches do not falsify a biological hypothesis.
"""
from __future__ import annotations
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen, HTTPRedirectHandler, build_opener
from pertinfer_benchmark.io import content_hash, write_json, read_json

RESOURCE_SPECS={
 'chembl_search':'(name, kind="molecule"|"target", limit=5): resolve IDs, not proof of identity.',
 'chembl_mechanism':'(molecule_id, limit=5): curated action types/targets/references, not the whole cellular chain.',
 'chembl_activity':'(molecule_id, target_id, limit=5): measured values, units, assay/document IDs; Kd/Ki/IC50/EC50 remain distinct.',
 'chembl_assay':'(assay_id): assay organism, format and cell details; distinguish binding from functional assays.',
 'bindingdb_affinity':'(uniprot, cutoff_nM=10000, limit=5): target measurements within a cutoff; may include other ligands. Match exact chemistry before using.',
 'gtop_query':'(kind="ligand"|"target", name OR identifier, interactions=false, limit=5): action/species/affinity records; requires host PERTAGENT_GTOP_API_KEY.',
 'omnipath_interactions':'(source OR target, resource="SIGNOR"|"CollecTRI"|"OmniPath", limit=5): human directed/signed prior interactions and citations, not task-specific proof.',
 'rhea_search':'(query, limit=5): curated reaction equations/ChEBI/EC/references; generic chemistry is not a measured task flux.',
 'uniprot_search':'(gene, taxon=9606, limit=5): reviewed protein identities/functions; taxonomy is protein origin, not experimental host.',
 'uniprot_entry':'(accession): function, catalytic/cofactor/location/interaction comments and original citations; no sequence dump.',
 'reactome_entry':'(stable_id): reaction participants/catalysts/compartments/references; distinguish inferred versus observed events.',
 'hpa_expression':'(ensembl_id): tissue/cell expression/localization background, not perturbation response or proof of absence.',
}
RESOURCE_RULES='''
Additional low-local-compute public research tools (OPEN additional-data only):
'''+ '\n'.join(k+v for k,v in RESOURCE_SPECS.items())+'''
All returned records are database annotations, not automatically matched direct
experiments. Follow assay/document/reference identifiers and disclose host/cell,
construct, compound form, dose/time, and measurement mismatches. A drug mechanism
label is not evidence for every downstream edge. Binding measurements do not
determine action sign. No hit, a cutoff-filtered result, absent expression or API
failure is not counterevidence. Signed databases can disagree; keep conflicts.
OmniPath/CollecTRI are human prior networks; do not call orthology-translated data
independent animal experiments. Packets are bounded summaries with raw SHA256
provenance and truncation flags, saved before you see them. No new graph edges,
probability calibration, biological scoring service or access to private data is
provided. This run cannot be called a masked-view or original-input comparison.
'''

HOSTS={'www.ebi.ac.uk','bindingdb.org','www.bindingdb.org','www.guidetopharmacology.org',
       'omnipathdb.org','www.rhea-db.org','rest.uniprot.org','reactome.org','www.proteinatlas.org'}
class RestrictedRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        parts=urlsplit(newurl)
        if parts.scheme!='https' or parts.hostname not in HOSTS:
            raise PermissionError('unregistered redirect blocked')
        # Never forward a GtoPdb credential to another origin.
        if req.has_header('Gtp-api-key') and parts.hostname!=urlsplit(req.full_url).hostname:
            raise PermissionError('credential redirect blocked')
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def identifier(value,pattern,label):
    if not isinstance(value,str) or not re.fullmatch(pattern,value):raise ValueError('invalid '+label)
    return value

def compact(value,*,list_limit=10,string_limit=2000):
    """Bound repeated context without changing measurement strings or signs."""
    changes=[]
    def walk(x,path):
        if isinstance(x,str) and len(x)>string_limit:
            changes.append(path);return x[:string_limit]+' [TRUNCATED; raw archived]'
        if isinstance(x,list):
            if len(x)>list_limit:changes.append(path)
            return [walk(v,path+'/'+str(i)) for i,v in enumerate(x[:list_limit])]
        if isinstance(x,dict):return {k:walk(v,path+'/'+k) for k,v in x.items() if k not in ('sequence','lineage','lineageIds')}
        return x
    return walk(value,''),changes

def source_evidences(rows,resource):
    """An integrated row's consensus is not a source-specific direction/sign."""
    if not isinstance(rows,list):raise ValueError('unexpected OmniPath response (not interactions)')
    selected=[]
    for row in rows:
        ev=row.get('evidences',{})
        if isinstance(ev,str):ev=json.loads(ev)
        scoped={}
        for kind in ('directed','negative','positive','undirected'):
            scoped[kind]=[e for e in ev.get(kind,[]) if
                (resource=='OmniPath' and e.get('dataset')=='omnipath') or
                (resource=='SIGNOR' and e.get('resource')=='SIGNOR') or
                (resource=='CollecTRI' and e.get('dataset')=='collectri')]
        if not any(scoped.values()):continue
        out={k:row.get(k) for k in ('source','target','source_genesymbol','target_genesymbol')}
        out.update(source_scoped_evidences=scoped,
                   source_scoped_direction=bool(scoped['directed'] or scoped['positive'] or scoped['negative']),
                   source_scoped_stimulation=bool(scoped['positive']),source_scoped_inhibition=bool(scoped['negative']),
                   source_scoped_references=sorted({str(p) for entries in scoped.values() for e in entries for p in e.get('references',[])}),
                   integrated_consensus_for_comparison={k:row.get(k) for k in ('is_directed','is_stimulation','is_inhibition')},
                   restriction='Only source_scoped fields belong to the requested source; integrated consensus may conflict.')
        selected.append(out)
    return selected

class ResearchTools:
    def __init__(self,directory,*,view='open',max_calls=24,max_per_tool=4,max_response_bytes=2_000_000,
                 max_total_bytes=12_000_000,max_packet_bytes=24000,max_total_packet_bytes=96000,opener=None,gtop_key=None):
        if view!='open':raise PermissionError('live research tools are OPEN additional-data only')
        self.root=Path(directory);self.root.mkdir(parents=True,exist_ok=True)
        self.opener=opener or build_opener(RestrictedRedirect()).open
        self.gtop_key=gtop_key if gtop_key is not None else os.environ.get('PERTAGENT_GTOP_API_KEY')
        self.limits=dict(max_calls=max_calls,max_per_tool=max_per_tool,max_response_bytes=max_response_bytes,
                         max_total_bytes=max_total_bytes,max_packet_bytes=max_packet_bytes,max_total_packet_bytes=max_total_packet_bytes)
        self.counts={};self.events=[];self.records={};self.network_requests=0;self.downloaded_bytes=0;self.packet_bytes=0;self.cache={}

    def availability(self):
        return {k:dict(configured=k!='gtop_query' or bool(self.gtop_key),
                       reason='requires PERTAGENT_GTOP_API_KEY' if k=='gtop_query' and not self.gtop_key else 'on-demand network query') for k in getattr(self,'tool_specs',RESOURCE_SPECS)}

    def _request(self,url,headers=None):
        parts=urlsplit(url)
        if parts.scheme!='https' or parts.hostname not in HOSTS or parts.username or parts.password:
            raise PermissionError('unregistered research URL')
        if url in self.cache:return self.cache[url]
        if self.downloaded_bytes>=self.limits['max_total_bytes']:raise ValueError('total download byte budget')
        self.network_requests+=1;started=time.monotonic()
        cap=min(self.limits['max_response_bytes'],self.limits['max_total_bytes']-self.downloaded_bytes)
        try:
            req=Request(url,headers={'User-Agent':'PertAgent-public-research/1',**(headers or {})})
            with self.opener(req) as response:raw=response.read(cap+1)
        except Exception as exc:
            # Deliberately omit request headers, response cookies and credentials.
            write_json(self.root/'failures'/f'{self.network_requests}.json',dict(url=url,error_type=type(exc).__name__,http_status=getattr(exc,'code',None),elapsed_seconds=time.monotonic()-started))
            raise RuntimeError('remote query failed: '+type(exc).__name__+'; result unknown') from None
        self.downloaded_bytes+=len(raw)
        digest=hashlib.sha256(raw).hexdigest();path=self.root/'raw'/digest
        path.parent.mkdir(exist_ok=True)
        if not path.exists():path.write_bytes(raw)
        origin=dict(url=url,sha256=digest,bytes=len(raw),retrieved_unix=time.time(),elapsed_seconds=time.monotonic()-started,
                    complete=len(raw)<=cap)
        write_json(self.root/'downloads'/(content_hash(url)+'.json'),origin)
        if len(raw)>cap:raise ValueError('response byte budget exceeded; partial bytes archived, not evidence')
        self.cache[url]=(raw,origin);return raw,origin

    def _json(self,url,headers=None):
        raw,origin=self._request(url,headers)
        if not raw:return {},origin
        return json.loads(raw),origin

    def _packet(self,tool,data,origins,limit):
        cap=min(self.limits['max_packet_bytes'],self.limits['max_total_packet_bytes']-self.packet_bytes-2500)
        if cap<1000:raise ValueError('cumulative evidence packet byte budget')
        body,truncated=compact(data,list_limit=limit)
        # If individual records are rich, reduce count deterministically; raw stays complete.
        if len(json.dumps(body,ensure_ascii=False).encode())>cap:
            body,more=compact(data,list_limit=1,string_limit=600);truncated+=more+['packet_byte_reduction']
        if len(json.dumps(body,ensure_ascii=False).encode())>cap:
            body={'summary_omitted':'record exceeds compact packet budget; raw archived','top_level_fields':list(data)[:30] if isinstance(data,dict) else []};truncated+=['packet_omitted']
        rid='research:'+content_hash(dict(tool=tool,body=body,origins=origins))
        record=dict(record_id=rid,channel='research_database',annotation=tool+' public database result; inspect assay applicability',
            details=body,origins=origins,evidence_ids=[o['url'] for o in origins],assertions=[],
            summary_truncation=truncated,limitations=['Database annotations are not task-matched direct experiments.',
            'Empty, unavailable, cutoff-filtered, or truncated results do not contradict a mechanism.',
            'Kd, Ki, IC50 and EC50 and functional actions are distinct; no automatic conversion.',
            'Expression, catalytic activity, binding, and cellular causal effects are distinct.'])
        size=len(json.dumps(record,ensure_ascii=False).encode())
        if self.packet_bytes+size>self.limits['max_total_packet_bytes']:raise ValueError('cumulative evidence packet byte budget')
        write_json(self.root/'packets'/(rid.split(':')[1]+'.json'),record);self.records[rid]=record;self.packet_bytes+=size
        return dict(record=record)

    def query(self,name,a):
        limit=min(max(int(a.get('limit',5)),1),10);origins=[]
        chembl='https://www.ebi.ac.uk/chembl/api/data/'
        if name=='chembl_search':
            kind=a.get('kind','molecule')
            if kind not in ('molecule','target'):raise ValueError('kind must be molecule or target')
            term=identifier(a['name'],r'[^\x00-\x1f]{1,100}','name')
            data,o=self._json(chembl+kind+'/search.json?'+urlencode(dict(q=term,limit=limit)))
        elif name in ('chembl_mechanism','chembl_activity','chembl_assay'):
            if name=='chembl_assay':
                aid=identifier(a['assay_id'],r'CHEMBL\d+','assay ID');url=chembl+'assay/'+aid+'.json'
            else:
                mid=identifier(a['molecule_id'],r'CHEMBL\d+','molecule ID');params=dict(molecule_chembl_id=mid,limit=limit)
                endpoint='mechanism'
                if name=='chembl_activity':
                    params['target_chembl_id']=identifier(a['target_id'],r'CHEMBL\d+','target ID');endpoint='activity'
                url=chembl+endpoint+'.json?'+urlencode(params)
            data,o=self._json(url)
        elif name=='bindingdb_affinity':
            acc=identifier(a['uniprot'],r'[A-Z0-9]{6,10}','UniProt accession')
            cutoff=int(a.get('cutoff_nM',10000))
            if not 1<=cutoff<=100000:raise ValueError('cutoff 1..100000 nM')
            data,o=self._json('https://bindingdb.org/rest/getLigandsByUniprots?'+urlencode(dict(uniprot=acc,cutoff=cutoff,response='application/json')))
            data=dict(query_uniprot=acc,cutoff_nM=cutoff,exact_ligand_not_filtered=True,data=data)
        elif name=='gtop_query':
            if not self.gtop_key:raise PermissionError('not_configured: PERTAGENT_GTOP_API_KEY is required')
            kind=a.get('kind','ligand')
            if kind not in ('ligand','target'):raise ValueError('kind ligand or target')
            base='https://www.guidetopharmacology.org/services/'+('ligands' if kind=='ligand' else 'targets')
            if a.get('identifier') is not None:
                ident=identifier(str(a['identifier']),r'\d{1,8}','GtoPdb ID');url=base+'/'+ident
                if a.get('interactions'):url+='/interactions'
            else:
                if a.get('interactions'):raise ValueError('resolve a unique identifier before requesting interactions')
                term=identifier(a['name'],r'[^\x00-\x1f]{1,100}','name');url=base+'?'+urlencode(dict(name=term))
            data,o=self._json(url,headers={'GTP-API-Key':self.gtop_key})
        elif name=='omnipath_interactions':
            resource=a.get('resource','SIGNOR')
            if resource not in ('SIGNOR','CollecTRI','OmniPath'):raise ValueError('resource must be SIGNOR, CollecTRI or OmniPath')
            params=dict(format='json',genesymbols='1',fields='sources,references,evidences',organisms='9606')
            if resource=='CollecTRI':params.update(datasets='collectri')
            else:params.update(datasets='omnipath',resources=resource)
            for key,param in [('source','sources'),('target','targets')]:
                if a.get(key):params[param]=identifier(a[key],r'[A-Za-z0-9_.:-]{1,50}',key)
            if not a.get('source') and not a.get('target'):raise ValueError('source or target required; whole network download disabled')
            data,o=self._json('https://omnipathdb.org/interactions?'+urlencode(params))
            data=dict(requested_resource=resource,organism=9606,orthology_translation_requested=False,
                      source_scoped=True,total_matching_interactions_before_packet_limit=len(data) if isinstance(data,list) else None,
                      interactions=source_evidences(data,resource))
        elif name=='rhea_search':
            term=identifier(a['query'],r'[^\x00-\x1f]{1,150}','Rhea query')
            raw,o=self._request('https://www.rhea-db.org/rhea/?'+urlencode(dict(query=term,columns='rhea-id,equation,chebi-id,ec,pubmed',format='tsv',limit=limit)))
            data=list(csv.DictReader(io.StringIO(raw.decode()),delimiter='\t'))
        elif name=='uniprot_search':
            gene=identifier(a['gene'],r'[A-Za-z0-9_.-]{1,50}','gene');taxon=int(a.get('taxon',9606))
            if not 1<=taxon<=20_000_000:raise ValueError('explicit positive NCBI taxon required')
            data,o=self._json('https://rest.uniprot.org/uniprotkb/search?'+urlencode(dict(query=f'(gene_exact:{gene}) AND (organism_id:{taxon}) AND (reviewed:true)',format='json',size=limit)))
        elif name=='uniprot_entry':
            acc=identifier(a['accession'],r'[A-Z0-9]{6,10}','UniProt accession')
            data,o=self._json('https://rest.uniprot.org/uniprotkb/'+acc+'.json')
        elif name=='reactome_entry':
            rid=identifier(a['stable_id'],r'R-(?:HSA|MMU|MMF)-\d+','Reactome stable ID')
            data,o=self._json('https://reactome.org/ContentService/data/query/'+rid)
        elif name=='hpa_expression':
            ensg=identifier(a['ensembl_id'],r'ENSG\d{11}','Ensembl gene ID')
            data,o=self._json('https://www.proteinatlas.org/'+ensg+'.json')
        else:raise ValueError('tool not available')
        origins.append(o);return self._packet(name,data,origins,limit)

    def call(self,name,args):
        began=time.monotonic();cached=False
        try:
            a=json.loads(args) if isinstance(args,str) else args
            if not isinstance(a,dict):raise ValueError('arguments must be an object')
            if name not in getattr(self,'tool_specs',RESOURCE_SPECS):raise ValueError('tool not available')
            if sum(self.counts.values())>=self.limits['max_calls'] or self.counts.get(name,0)>=self.limits['max_per_tool']:raise ValueError('research query count budget')
            self.counts[name]=self.counts.get(name,0)+1
            key=content_hash(dict(tool=name,arguments=a));path=self.root/'responses'/(key+'.json')
            if path.exists():
                value=read_json(path);self.records[value['record']['record_id']]=value['record'];cached=True
            else:
                value=self.query(name,a);write_json(path,value)
            status='ok'
        except Exception as exc:
            value=dict(error=str(exc),evidence_verdict='unknown');status='not_configured' if 'not_configured:' in str(exc) else 'error'
        self.events.append(dict(tool=name,arguments=args,result=value,status=status,cache_hit=cached,elapsed_seconds=time.monotonic()-began))
        write_json(self.root/'events.json',self.events);return value
