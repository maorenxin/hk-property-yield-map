from __future__ import annotations
import json, re, time, subprocess
from pathlib import Path
import pandas as pd
from pyproj import Transformer

ROOT=Path(__file__).resolve().parents[1]
RAW=ROOT/'data/raw/centaline'
DER=ROOT/'data/derived'
CACHE=RAW/'hma_cache'
CACHE.mkdir(exist_ok=True)
BASE='https://census.centamap.com/zh-TW/Region/List/hma-{hma}.html?field=t_pop&sort=default'
transformer=Transformer.from_crs(2326,4326,always_xy=True)

def get_grid(hma):
    p=CACHE/f'{hma}.html'
    if not p.exists():
        u=BASE.format(hma=hma)
        subprocess.run(['/usr/bin/curl','-L','--max-time','40','-A','Mozilla/5.0','-sS',u,'-o',str(p)],check=True)
        time.sleep(0.08)
    s=p.read_text(errors='ignore')
    m=re.search(r'var grid =(\[.*?\]);\s*var',s,re.S)
    if not m:
        m=re.search(r'var grid =(\[.*?\]);',s,re.S)
    if not m: return []
    try: return json.loads(m.group(1))
    except Exception: return []

def main():
    raw=[]
    hmas=set()
    for kind in ['sale','rent']:
        d=json.loads((RAW/f'{kind}_sample_1000.json').read_text())
        for x in d['records']:
            scope=x.get('scope') or {}; bg=x.get('bldgGrp') or {}
            h=scope.get('hma_id'); c=bg.get('bldgGrpId')
            hmas.add(h)
            raw.append({'kind':kind,'source_record_id':x.get('id'),'bldg_grp_id':c,'hma_id':h,'estate_name':x.get('bigEstateName') or x.get('estateName'),'district_raw':scope.get('db'),'district':scope.get('db'),'transaction_price_hkd':x.get('transactionPrice'),'saleable_area_sqft':x.get('nArea'),'unit_price_hkd_sqft_published':x.get('nUnitPrice'),'ins_date':x.get('insDate'),'month':pd.to_datetime(x.get('insDate')).strftime('%Y-%m-01'),'detail_url':x.get('detailUrl'),'address_raw':(x.get('displayText') or {}).get('addr',{}).get('line5'),'hma':scope.get('hma')})
    coords={}; pages=0
    for h in sorted(hmas):
        if not h: continue
        for row in get_grid(h):
            code=row.get('Code'); x=row.get('X'); y=row.get('Y')
            if code and x not in (None,'','0') and y not in (None,'','0'):
                try:
                    lon,lat=transformer.transform(float(x),float(y))
                    if 113<lon<115 and 21<lat<24: coords[(h,code)]={'lon':lon,'lat':lat,'coord_source':f'https://census.centamap.com/zh-TW/Region/List/hma-{h}.html','coord_confidence':'building-group grid coordinate'}
                except Exception: pass
        pages+=1
    df=pd.DataFrame(raw)
    df['coord_key']=list(zip(df['hma_id'],df['bldg_grp_id']))
    df['lon']=df['coord_key'].map(lambda k:(coords.get(k) or {}).get('lon'))
    df['lat']=df['coord_key'].map(lambda k:(coords.get(k) or {}).get('lat'))
    df['coord_source']=df['coord_key'].map(lambda k:(coords.get(k) or {}).get('coord_source'))
    df['coord_confidence']=df['coord_key'].map(lambda k:(coords.get(k) or {}).get('coord_confidence'))
    df['size_band']=pd.cut(df['saleable_area_sqft'],bins=[0,400,600,800,1200,float('inf')],labels=['<400','400-599','600-799','800-1199','1200+'],right=False)
    df['unit_price_calc']=df['transaction_price_hkd']/df['saleable_area_sqft']
    df.to_csv(DER/'centaline_detail_with_coords.csv',index=False)
    sale=df[df.kind=='sale']; rent=df[df.kind=='rent']
    keys=['bldg_grp_id','hma_id','size_band','month']
    sc=sale.groupby(keys,observed=True).agg(sale_price_hkd_sqft=('unit_price_calc','median'),sale_n=('source_record_id','count'),lon=('lon','median'),lat=('lat','median'),estate_name=('estate_name','first'),district=('district_raw','first')).reset_index()
    rc=rent.groupby(keys,observed=True).agg(rent_monthly_hkd_sqft=('unit_price_calc','median'),rent_n=('source_record_id','count')).reset_index()
    cells=sc.merge(rc,on=keys,how='inner')
    cells['gross_yield_pct']=cells['rent_monthly_hkd_sqft']*12/cells['sale_price_hkd_sqft']*100
    cells['price_to_rent_years']=cells['sale_price_hkd_sqft']/(cells['rent_monthly_hkd_sqft']*12)
    cells['pair_quality']=cells.apply(lambda r:'cell_n>=5_each' if r.sale_n>=5 and r.rent_n>=5 else 'low_sample',axis=1)
    cells.to_csv(DER/'centaline_building_matched_cells.csv',index=False)
    points=cells.groupby(['bldg_grp_id','hma_id'],as_index=False).agg(
      gross_yield_pct=('gross_yield_pct','median'),price_to_rent_years=('price_to_rent_years','median'),
      sale_price_hkd_sqft=('sale_price_hkd_sqft','median'),rent_monthly_hkd_sqft=('rent_monthly_hkd_sqft','median'),
      matched_cells=('gross_yield_pct','count'),sale_n=('sale_n','sum'),rent_n=('rent_n','sum'),
      lon=('lon','median'),lat=('lat','median'),estate_name=('estate_name','first'),district=('district','first'),
      period_start=('month','min'),period_end=('month','max'))
    points=points[points.lon.notna() & points.lat.notna()].copy()
    points.to_csv(DER/'centaline_building_points.csv',index=False)
    feats=[]
    for _,r in points.iterrows():
      feats.append({'type':'Feature','properties':{k:(None if pd.isna(v) else v) for k,v in r.to_dict().items() if k not in ['lon','lat']},'geometry':{'type':'Point','coordinates':[float(r.lon),float(r.lat)]}})
    (DER/'centaline_building_points.geojson').write_text(json.dumps({'type':'FeatureCollection','features':feats},ensure_ascii=False,separators=(',',':')))
    manifest={'hmas':len(hmas),'hma_pages':pages,'coords':len(coords),'raw_records':len(df),'records_with_coords':int(df.lon.notna().sum()),'matched_cells':len(cells),'building_points':len(points)}
    (RAW/'coordinate_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print(json.dumps(manifest,ensure_ascii=False,indent=2))

if __name__=='__main__': main()

