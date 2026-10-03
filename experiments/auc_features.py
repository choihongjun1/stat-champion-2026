"""Frozen causal temporal features. Exact calendar-quarter lags, no backfill."""
import numpy as np
import pandas as pd

SOURCES = ('trdar_flow_pop', 'trdar_biz_sales_per_store_observed',
           'trdar_biz_store_cnt_observed')


def temporal_features(df, sources=SOURCES):
    q = pd.PeriodIndex(df.origin, freq='Q').asi8
    keys = pd.MultiIndex.from_arrays([df.store_id, q])
    if not keys.is_unique:
        raise ValueError('Duplicate entity/origin keys')
    out = pd.DataFrame(index=df.index)
    groups = {'A1': [], 'A2': [], 'A3': []}
    sources = list(sources)
    if 'online_blog_cnt_3m' in df:
        sources.append('online_blog_cnt_3m')
    for c in sources:
        s = pd.Series(df[c].to_numpy(dtype=float, na_value=np.nan), index=keys)
        hist = np.column_stack([s.reindex(pd.MultiIndex.from_arrays(
            [df.store_id, q-k])).to_numpy() for k in range(8)])
        # Missing calendar quarters remain missing, rather than becoming adjacent.
        for w in (4, 8):
            h = hist[:, :w]
            valid = np.isfinite(h)
            n = valid.sum(axis=1)
            x = -np.arange(w, dtype=float)
            sx = (valid*x).sum(axis=1)
            sy = np.where(valid, h, 0).sum(axis=1)
            sxx = (valid*x*x).sum(axis=1)
            sxy = np.where(valid, h*x, 0).sum(axis=1)
            denom = n*sxx-sx*sx
            slope = np.divide(n*sxy-sx*sy, denom, out=np.full(len(df),np.nan), where=(n>=3)&(denom>0))
            name = f'exp_{c}_slope{w}'
            out[name] = slope
            groups['A3' if c.startswith('online') else 'A1'].append(name)
        h = hist[:, :4]
        valid = np.isfinite(h)
        n = valid.sum(axis=1)
        mean = np.divide(np.where(valid,h,0).sum(axis=1),n,out=np.full(len(df),np.nan),where=n>=3)
        var = np.divide(np.where(valid,(h-mean[:,None])**2,0).sum(axis=1),n,out=np.full(len(df),np.nan),where=n>=3)
        peak = np.max(np.where(valid,h,-np.inf),axis=1)
        vals = {
            'ratio4': np.divide(h[:,0],mean,out=np.full(len(df),np.nan),where=np.isfinite(mean)&(np.abs(mean)>1e-9)),
            'volatility4': np.sqrt(var),
            'peak_drop4': np.divide(peak-h[:,0],np.abs(peak),out=np.full(len(df),np.nan),where=(n>=3)&np.isfinite(peak)&(np.abs(peak)>1e-9)),
        }
        for suffix, value in vals.items():
            name=f'exp_{c}_{suffix}'
            out[name]=value
            groups['A3' if c.startswith('online') else ('A1' if suffix=='ratio4' else 'A2')].append(name)
    # License attribute missingness only. Spatial/coverage/collection QA excluded.
    out['exp_area_missing'] = df.area.isna().astype(float)
    groups['B'] = ['exp_area_missing']
    return out, groups
