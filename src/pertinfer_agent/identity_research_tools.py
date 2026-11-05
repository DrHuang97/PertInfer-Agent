"""Diagnostic v2: verified gene IDs before expression/pathway lookup.

Original v1 tools and trials remain byte-for-byte unchanged.
"""
import copy
import re
from urllib.parse import urlencode
from .research_tools import ResearchTools,RESOURCE_SPECS as ORIGINAL_SPECS,RESOURCE_RULES as ORIGINAL_RULES,identifier

RESOURCE_SPECS=copy.deepcopy(ORIGINAL_SPECS)
RESOURCE_SPECS['hpa_expression']='(gene, limit=5; optional ensembl_id): resolve reviewed HUMAN UniProt gene to an unambiguous Ensembl gene ID, verify returned HPA gene identity; prefer gene over a memorized ID.'
RESOURCE_SPECS['uniprot_search']='(gene, taxon=9606, limit=5): verified gene/protein identity plus explicit ensembl_gene_ids and Reactome IDs so they survive summary truncation.'
RESOURCE_SPECS['reactome_entry']='(stable_id OR gene, limit=5): resolve human gene through UniProt Reactome cross-references when stable ID is unknown; returns candidate pathways, not proof of a specific reaction.'
RESOURCE_RULES=ORIGINAL_RULES+'\nIDENTITY RESOLUTION V2 overrides the same tool arguments above:\n'+'\n'.join(k+': '+RESOURCE_SPECS[k] for k in ['hpa_expression','uniprot_search','reactome_entry'])+'\nPrefer resolved identifiers. Confirm returned gene names and pathway titles; a valid identifier may still identify the wrong entity or a broad pathway.\n'

def summary(row):
    xrefs=row.get('uniProtKBCrossReferences',[])
    ensembl=sorted({p['value'].split('.')[0] for x in xrefs if x.get('database')=='Ensembl' for p in x.get('properties',[]) if p.get('key')=='GeneId' and p.get('value','').startswith('ENSG')})
    genes=[g.get('geneName',{}).get('value') for g in row.get('genes',[])]
    return dict(accession=row.get('primaryAccession'),gene_names=genes,organism=row.get('organism'),protein_name=row.get('proteinDescription',{}).get('recommendedName',{}),
                ensembl_gene_ids=ensembl,reactome_ids=[dict(id=x['id'],properties=x.get('properties',[])) for x in xrefs if x.get('database')=='Reactome'],
                entry_type=row.get('entryType'),comments=row.get('comments',[])[:5])

class IdentityResearchTools(ResearchTools):
    def resolve(self,gene,taxon):
        gene=identifier(gene,r'[A-Za-z0-9_.-]{1,50}','gene')
        data,origin=self._json('https://rest.uniprot.org/uniprotkb/search?'+urlencode(dict(query=f'(gene_exact:{gene}) AND (organism_id:{int(taxon)}) AND (reviewed:true)',format='json',size=5)))
        rows=[summary(x) for x in data.get('results',[])]
        return rows,origin

    def query(self,name,a):
        limit=min(max(int(a.get('limit',5)),1),10)
        if name=='uniprot_search':
            rows,origin=self.resolve(a['gene'],int(a.get('taxon',9606)))
            return self._packet(name,dict(results=rows,identity_fields_explicit=True),[origin],limit)
        if name=='hpa_expression' and a.get('gene'):
            rows,origin=self.resolve(a['gene'],9606)
            exact=[r for r in rows if any(g and g.upper()==a['gene'].upper() for g in r['gene_names']) and r['organism']['taxonId']==9606]
            ids=sorted({x for r in exact for x in r['ensembl_gene_ids']})
            if len(ids)!=1:raise ValueError('gene resolution absent or ambiguous; do not guess an Ensembl ID')
            if a.get('ensembl_id') and a['ensembl_id']!=ids[0]:raise ValueError('provided Ensembl ID does not match verified gene')
            data,hpa=self._json('https://www.proteinatlas.org/'+ids[0]+'.json')
            if data.get('Gene','').upper()!=a['gene'].upper() or data.get('Ensembl')!=ids[0]:raise ValueError('HPA identity mismatch; not usable as target evidence')
            return self._packet(name,dict(identity_verified=True,requested_gene=a['gene'],resolved_ensembl_id=ids[0],uniprot_resolution=exact,expression_record=data),[origin,hpa],limit)
        if name=='reactome_entry' and a.get('gene'):
            rows,origin=self.resolve(a['gene'],9606)
            return self._packet(name,dict(gene=a['gene'],resolved_gene_records=rows,resolution_only=True,
                next_step='Select an applicable Reactome ID and call reactome_entry(stable_id). Pathway membership does not itself prove a specific reaction.'),[origin],limit)
        return super().query(name,a)
