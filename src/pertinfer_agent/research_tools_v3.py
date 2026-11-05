"""Versioned identity-safe research interface. Original v1/v2 sources unchanged."""
import copy
import json
import subprocess
from pathlib import Path
from urllib.parse import urlencode
from .identity_research_tools import IdentityResearchTools, summary
from .research_tools import RESOURCE_SPECS as BASE_SPECS, RESOURCE_RULES, identifier, source_evidences

RESOURCE_SPECS = copy.deepcopy(BASE_SPECS)
RESOURCE_SPECS.update({
 'pubmed_record': '(pmid): exact numeric PMID identity check through Europe PMC; original title/abstract/DOI/PMCID and publication types. Use when paper identity/context is unresolved; an abstract is not full-text figure evidence. No arbitrary URL or broad literature search.',
 'hpa_expression': '(gene, optional ensembl_id): resolve exact reviewed human gene and verify HPA response; gene REQUIRED. Baseline expression is not perturbation/activity evidence.',
 'uniprot_search': '(gene, taxon=9606, limit=5): identity, explicit Ensembl gene IDs and Reactome IDs; check exact primary gene and protein taxon.',
 'uniprot_entry': '(accession): compact named identity/function/catalytic/cofactor/location comments and cross-reference IDs.',
 'reactome_entry': '(gene OR stable_id, expected_title optional): gene resolves reviewed human UniProt pathway IDs without guessing; stable_id fetches event and checks title when supplied. Broad membership is not reaction proof.',
 'omnipath_interactions': '(source OR target, resource=SIGNOR|CollecTRI|OmniPath, limit=5): if BOTH endpoints supplied, require BOTH to match before truncating. Source-specific sign/references/attrs only; default-activation annotation is not a measured causal sign.',
 'bindingdb_affinity': '(uniprot, monomer_id OR ligand_smiles, cutoff_nM=10000, limit=5): filter ALL downloaded rows by exact ligand ID and/or canonical isomeric chemical structure before limiting. Requires configured RDKit for SMILES; keeps stereochemistry/charge/salts. ID alone does not verify chemistry.'})

# This is the only new catalog; do not append outdated v1 argument descriptions.
RULES = '''Public additional-data tools, not task-matched experimental truth. Empty,
truncated or failed results are unknown. Binding, expression and catalytic activity
are distinct. Preserve Ki/Kd/IC50/EC50, units, qualifiers and original references.
OmniPath sign/PMIDs must come from requested source evidences, not consensus.
Resolve identity before inference. Never guess a stable identifier when a gene or
ligand resolver is available. Report host/cell/construct/dose/time mismatches.
''' + '\n'.join(k+v for k,v in RESOURCE_SPECS.items())

class ResearchToolsV3(IdentityResearchTools):
    tool_specs=RESOURCE_SPECS
    def __init__(self,*args,chemistry_python=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.chemistry_python=Path(chemistry_python).absolute() if chemistry_python else None

    def exact_chemical_rows(self,smiles,rows):
        if not self.chemistry_python or not self.chemistry_python.is_file():
            raise ValueError('not_configured: exact SMILES filtering requires host RDKit chemistry_python; use a verified monomer_id or request runtime configuration')
        if not isinstance(smiles,str) or not 1<=len(smiles)<=2000:raise ValueError('invalid ligand SMILES size')
        script='''import sys,json
from rdkit import Chem,rdBase
q=json.load(sys.stdin)
def canonical(s):
 m=Chem.MolFromSmiles(s) if isinstance(s,str) else None
 return Chem.MolToSmiles(m,isomericSmiles=True,canonical=True) if m is not None else None
target=canonical(q['target'])
if target is None:raise ValueError('invalid target SMILES')
valid=[canonical(s) for s in q['rows']]
print(json.dumps(dict(matches=[i for i,s in enumerate(valid) if s==target],invalid_rows=sum(s is None for s in valid),rdkit_version=rdBase.rdkitVersion,canonical_target=target)))
'''
        data=dict(target=smiles,rows=[r.get('smile') for r in rows])
        proc=subprocess.run([str(self.chemistry_python),'-c',script],input=json.dumps(data),capture_output=True,text=True)
        if proc.returncode:raise ValueError('chemical identity filter failed; no affinity conclusion')
        result=json.loads(proc.stdout)
        return [rows[i] for i in result['matches']],dict(result,comparison='exact RDKit canonical isomeric SMILES, not similarity; no salt stripping/protonation/tautomer normalization')

    def _packet(self,tool,data,origins,limit):
        # A query row limit must not also truncate the PMIDs, sign evidences or
        # identifier fields INSIDE the chosen row. The original wrapper applied
        # one list limit recursively to every array.
        data=copy.deepcopy(data);cuts={}
        if isinstance(data,list):
            if len(data)>limit:cuts['/']=dict(total=len(data),returned=limit)
            data=data[:limit]
        elif isinstance(data,dict):
            for key in ('results','activities','molecules','targets','mechanisms','interactions','rows'):
                if isinstance(data.get(key),list):
                    if len(data[key])>limit:cuts['/'+key]=dict(total=len(data[key]),returned=limit)
                    data[key]=data[key][:limit]
        out=super()._packet(tool,data,origins,max(30,limit))
        if cuts:
            out['record']['query_row_limits']=cuts
            out['record']['summary_truncation']+=['query_row_limit:'+key for key in cuts]
            from pertinfer_benchmark.io import write_json
            write_json(self.root/'packets'/(out['record']['record_id'].split(':')[1]+'.json'),out['record'])
        return out

    def query(self, name, a):
        if name=='pubmed_record':
            pmid=identifier(str(a['pmid']),r'[0-9]{1,12}','numeric PMID')
            data,o=self._json('https://www.ebi.ac.uk/europepmc/webservices/rest/search?'+urlencode(dict(query='EXT_ID:'+pmid+' AND SRC:MED',format='json',resultType='core',pageSize=1)))
            rows=data.get('resultList',{}).get('result',[])
            if len(rows)!=1 or str(rows[0].get('id'))!=pmid or rows[0].get('source')!='MED':raise ValueError('exact PMID identity unresolved; returned record not accepted')
            r=rows[0]
            fields=('id','source','pmid','pmcid','doi','title','authorString','pubYear','journalInfo','abstractText','pubTypeList','isOpenAccess','isRetracted','fullTextUrlList','firstPublicationDate')
            return self._packet(name,dict(requested_pmid=pmid,identity_verified=True,bibliographic_record={k:r[k] for k in fields if k in r},evidence_scope='Bibliographic identity and abstract only; does not independently verify figure measurements, complete mechanism, or exact experimental conditions. HTML tags may occur in original abstract text.'),[o],10)
        if name == 'hpa_expression' and not a.get('gene'):
            raise ValueError('gene is required; Ensembl-only lookup cannot verify intended identity. Example: {"gene":"NCOA2"}')
        if name == 'uniprot_entry':
            acc=identifier(a['accession'],r'[A-Z0-9]{6,10}','UniProt accession')
            data,o=self._json('https://rest.uniprot.org/uniprotkb/'+acc+'.json')
            # Named fields survive even when the original cross-reference array is long.
            return self._packet(name,summary(data),[o],min(max(int(a.get('limit',5)),1),10))
        if name == 'reactome_entry':
            if a.get('gene') and not a.get('stable_id'):
                rows,o=self.resolve(a['gene'],9606)
                rows=[r for r in rows if r.get('organism',{}).get('taxonId')==9606 and any(g and g.upper()==a['gene'].upper() for g in r['gene_names'])]
                if not rows:raise ValueError('exact reviewed human gene unresolved; do not guess a pathway ID')
                ids=sorted({x['id'] for r in rows for x in r['reactome_ids']})
                return self._packet(name,dict(gene=a['gene'],identity_verified=True,reactome_ids=ids,resolution_only=True,
                    next_step='Use a returned stable_id to fetch event details and verify its displayName/species; membership alone does not prove a reaction.'),[o],10)
            rid=identifier(a['stable_id'],r'R-(?:HSA|MMU|MMF)-\d+','Reactome stable ID')
            data,o=self._json('https://reactome.org/ContentService/data/query/'+rid)
            actual=data.get('stId')
            if actual and actual!=rid:raise ValueError('Reactome stable ID mismatch')
            title=data.get('displayName','')
            if a.get('expected_title') and a['expected_title'].casefold()!=title.casefold():
                raise ValueError('Reactome title mismatch: '+title+'; this event is not the requested title')
            return self._packet(name,data,[o],min(max(int(a.get('limit',5)),1),10))
        if name == 'omnipath_interactions':
            resource=a.get('resource','SIGNOR')
            if resource not in ('SIGNOR','CollecTRI','OmniPath'):raise ValueError('unknown signed interaction resource')
            params=dict(format='json',genesymbols='1',fields='sources,references,evidences',organisms='9606')
            if resource=='CollecTRI':params.update(datasets='collectri')
            else:params.update(datasets='omnipath',resources=resource)
            endpoints={}
            for key,param in [('source','sources'),('target','targets')]:
                if a.get(key):
                    endpoints[key]=identifier(a[key],r'[A-Za-z0-9_.:-]{1,50}',key);params[param]=endpoints[key]
            if not endpoints:raise ValueError('source or target required; full network download disabled')
            rows,o=self._json('https://omnipathdb.org/interactions?'+urlencode(params))
            if not isinstance(rows,list):raise ValueError('unexpected interaction response')
            matching=[r for r in rows if all(str(value).casefold() in {str(r.get(k,'')).casefold(),str(r.get(k+'_genesymbol','')).casefold()} for k,value in endpoints.items())]
            interactions=source_evidences(matching,resource)
            return self._packet(name,dict(query_endpoints=endpoints,requested_resource=resource,organism=9606,
                raw_rows=len(rows),endpoint_matching_rows=len(matching),source_matching_rows=len(interactions),
                query_semantics='ALL specified endpoints must match exact accession or symbol; host filters before summary limit',
                orthology_translation_requested=False,interactions=interactions,
                restriction='Source-scoped fields only. Read attrs.sign_decision: default activation is an annotation convention, not necessarily experimental sign evidence. Missing/truncated rows are unknown.'),[o],min(max(int(a.get('limit',5)),1),10))
        if name == 'bindingdb_affinity':
            acc=identifier(a['uniprot'],r'[A-Z0-9]{6,10}','UniProt accession')
            mid=identifier(str(a['monomer_id']),r'\d{1,12}','BindingDB monomer_id') if a.get('monomer_id') is not None else None
            if not mid and not a.get('ligand_smiles'):raise ValueError('monomer_id OR ligand_smiles required; target-only ligands are not the requested drug')
            if a.get('ligand_smiles') and (not self.chemistry_python or not self.chemistry_python.is_file()):raise ValueError('not_configured: host RDKit chemistry_python needed for exact chemical filtering')
            cutoff=int(a.get('cutoff_nM',10000))
            if not 1<=cutoff<=100000:raise ValueError('cutoff 1..100000 nM')
            data,o=self._json('https://bindingdb.org/rest/getLigandsByUniprots?'+urlencode(dict(uniprot=acc,cutoff=cutoff,response='application/json')))
            rows=data.get('getLindsByUniprotsResponse',{}).get('affinities',[])
            if not isinstance(rows,list):raise ValueError('unexpected BindingDB response schema')
            selected=[r for r in rows if str(r.get('monomerid'))==mid] if mid else rows
            chemical_identity=None
            if a.get('ligand_smiles'):selected,chemical_identity=self.exact_chemical_rows(a['ligand_smiles'],selected)
            limit=min(max(int(a.get('limit',5)),1),10)
            return self._packet(name,dict(query_uniprot=acc,monomer_id=mid,cutoff_nM=cutoff,
                all_rows_scanned=len(rows),matched_rows=len(selected),returned_rows=min(limit,len(selected)),
                rows=selected[:limit],chemical_identity=chemical_identity,identity_status='exact canonical isomeric structure' if chemical_identity else 'exact database ligand ID; chemical identity still requires structure verification',
                empty_means='unknown; no result within this cutoff/download, not nonbinding'),[o],limit)
        return super().query(name,a)

    def call(self,name,args):
        # Failed queries remain in the ledger; provide actionable repair without
        # exposing arbitrary URLs or automatically retrying network requests.
        before_requests=self.network_requests
        out=super().call(name,args)
        if 'error' in out:
            failure=self.root/'failures'/f'{self.network_requests}.json'
            if self.network_requests>before_requests and failure.is_file():
                from pertinfer_benchmark.io import read_json
                detail=read_json(failure)
                out['remote_error']=dict(error_type=detail.get('error_type'),http_status=detail.get('http_status'),biological_verdict='unknown',retryable_transport_status=detail.get('http_status') in (429,500,502,503,504))
            out['tool_contract']=RESOURCE_SPECS.get(name,'unknown tool')
            out['next_action']='For remote_error, keep the result unknown; do not repair a valid entity ID or infer biological absence from an upstream HTTP failure. No automatic retry. For a local argument error, repair arguments using tool_contract.'
            self.events[-1]['result']=out
            from pertinfer_benchmark.io import write_json
            write_json(self.root/'events.json',self.events)
        return out
