import xml.etree.ElementTree as ET
from .config import RELEASES_URLS
from tinydb import TinyDB, Query
from tinydb.storages import MemoryStorage
from random import Random
import pandas as pd
from collections import namedtuple
from functools import reduce
from operator import __and__
from .downloader import get_release_datasets_dir, get_dataset_files


PANDAS_CONTAINER = namedtuple('PANDAS_CONTAINER', ['edf', 'odf', 'mdf', 'ldf'])

TRIPLE_KEYS = ['subject', 'predicate', 'object']


def load(release):

    if release not in RELEASES_URLS:
        raise ValueError('{} not in in {}'.format(release,
                         list(RELEASES_URLS.keys())))

    db = TinyDB(storage=MemoryStorage)

    for dataset_name in get_release_datasets_dir(release):

        for filepath in get_dataset_files(release, dataset_name):

            entries_dicts = read_webnlg_file(dataset_name, filepath)

            db.insert_multiple(entries_dicts)

    return WebNLGCorpus(release, db)


def make_dict_from_triple(triple_text):

    triple_dict = {'text': triple_text}

    for triple_key, part in zip(TRIPLE_KEYS, triple_text.split('|')):

        stripped_part = part.strip()

        triple_dict[triple_key] = stripped_part

    return triple_dict


def make_dict_from_entity(entity_text):

    entity_placeholder, entity_value = entity_text.split('|')

    yield entity_placeholder.strip(), entity_value.strip()


def read_webnlg_file(dataset, filepath):

    entries_dicts = []

    tree = ET.parse(filepath)
    root = tree.getroot()

    for entry in root.iter('entry'):

        mtriples = entry.find('modifiedtripleset').findall('mtriple')
        otriples = entry.find('originaltripleset').findall('otriple')
        ntriples = int(entry.attrib['size'])

        idx = "{dataset}_{category}_{ntriples}_{eid}".format(
               dataset=dataset,
               category=entry.attrib['category'],
               ntriples=ntriples,
               eid=entry.attrib['eid'])

        entry_dict = {
            "dataset": dataset,
            "idx": idx,
            "category": entry.attrib['category'],
            "eid": entry.attrib['eid'],
            "ntriples": ntriples,
            "content": ET.tostring(entry),
            "otriples": [make_dict_from_triple(e.text) for e in otriples],
            "mtriples": [make_dict_from_triple(e.text) for e in mtriples]
        }

        if 'v1.2' in filepath:

            entry_dict["lexes"] = [
                {
                        'text': e.findtext('text'),
                        'template': e.findtext('template', 'NOT-FOUND'),
                        'comment': e.attrib['comment'],
                        'lid': e.attrib['lid']
                } for e in entry.findall('lex')
            ]

            entry_dict["entity_map"] = {
                make_dict_from_entity(entity.text)
                for entity in entry.find('entitymap').findall('entity')
            }

            entry_dict["delexicalized_mtriples"] = [
                    {
                        'subject': entry_dict["entity_map"][d['subject']],
                        'predicate': d['predicate'],
                        'object': entry_dict["entity_map"][d['object']]
                    } for d in entry_dict["mtriples"]
            ]

        else:

            entry_dict["lexes"] = [
                {
                        'text': e.text,
                        'comment': e.attrib['comment'],
                        'lid': e.attrib['lid']
                } for e in entry.findall('lex')
            ]

        entries_dicts.append(entry_dict)

    return entries_dicts


class WebNLGEntry(object):

    def __init__(self, entry):

        self._entry = entry

        if 'entity_map' in self._entry:
            self._delexicalize_map = {
                    v: k for k, v in self._entry['entity_map'].items()
                    }

    @property
    def data(self):

        return self._entry['mtriples']

    @property
    def delexicalized_data(self):

        return self._entry["delexicalized_mtriples"]

    @property
    def lexes(self):

        for l in self._entry["lexes"]:

            yield l['text']

    @property
    def templates(self):

        for l in self._entry["lexes"]:

            yield l['template']

    @property
    def idx(self):

        return self._entry['idx']

    @property
    def eid(self):

        return self._entry['eid']

    @property
    def category(self):

        return self._entry['category']

    def __str__(self):

        lines = []

        lines.append(f"Entry info: Category={self.category} "
                     "eid={eid} idx={idx}\n".format(
                             eid=self.eid,
                             idx=self.idx
                             )
                     )

        lines.append("\tModified Triples:\n")
        lines.extend([m['text'] for m in self._entry['mtriples']])
        lines.append("\n")

        lines.append("\tLexicalizations:\n")
        lines.extend([
                '{}\n{}\n'.format(
                        l['text'], l.get('template', '')
                        ) for l in self._entry['lexes']
                ]
        )

        if 'entity_map' in self._entry:
            lines.append('\tEntity map:\n')
            lines.extend([
                    f'{k} : {v}'
                    for k, v in self._entry['entity_map'].items()
                    ]
            )

        return "\n".join(lines)

    def __repr__(self):

        return self.__str__()


class WebNLGCorpus(object):

    def __init__(self, release, db):

        self._release = release
        self._db = db
        self._query = Query()

    @property
    def release(self):

        return self._release

    def subset(self, ntriples=None, categories=None, datasets=None):

        if ntriples is None and categories is None and datasets is None:
            raise ValueError('At least one filter must be informed.')

        filters = []

        if ntriples:
            filters.append(self._query.ntriples.one_of(ntriples))

        if categories:
            filters.append(self._query.category.one_of(categories))

        if datasets:
            filters.append(self._query.dataset.one_of(datasets))

        subset_db = TinyDB(storage=MemoryStorage)
        subset_db.insert_multiple(self._db.search(reduce(__and__, filters)))

        return WebNLGCorpus(self.release, subset_db)

    def sample(self, eid=None, categories=None, ntriples=None, idx=None,
               seed=None, datasets=None):

        rg = Random()
        rg.seed(seed)

        filters = []

        if eid or categories or ntriples or idx or datasets:

            if idx:
                filters.append(self._query.idx == idx)
            if eid:
                filters.append(self._query.eid == eid)
            if categories:
                filters.append(self._query.category.one_of(categories))
            if ntriples:
                filters.append(self._query.ntriples.one_of(ntriples))
            if datasets:
                filters.append(self._query.dataset.one_of(datasets))

            sample_entry = rg.choice(self._db.search(reduce(__and__, filters)))

        else:
            sample_entry = rg.choice(list(self._db))

        return WebNLGEntry(sample_entry)

    @property
    def edf(self):

        return self.as_pandas.edf

    @property
    def odf(self):

        return self.as_pandas.odf

    @property
    def mdf(self):

        return self.as_pandas.mdf

    @property
    def ldf(self):

        return self.as_pandas.ldf

    def __get_item__(self, idx):

        results = self._db.search(self._query.idx == idx)

        if results:
            return WebNLGEntry(results[0])

        return None

    @property
    def as_pandas(self):

        if hasattr(self, '_pandas'):

            return self._pandas

        entries_dicts = []
        otriples_dicts = []
        mtriples_dicts = []
        lexes_dicts = []

        for entry in self._db:

            entry_dict = {
                "idx": entry['idx'],
                "dataset": entry['dataset'],
                "category": entry['category'],
                "eid": entry['eid'],
                "ntriples": entry['ntriples'],
                "content": entry['content']
            }
            entries_dicts.append(entry_dict)

            otriple_dict = [
                {
                    'idx': entry['idx'],
                    'dataset': entry['dataset'],
                    'category': entry['category'],
                    'text': ot['text'],
                    'object': ot['object'],
                    'predicate': ot['predicate'],
                    'subject': ot['subject']
                } for ot in entry['otriples']
            ]
            otriples_dicts.extend(otriple_dict)

            mtriple_dict = [
                {
                    'idx': entry['idx'],
                    'dataset': entry['dataset'],
                    'category': entry['category'],
                    'text': mt['text'],
                    'object': mt['object'],
                    'predicate': mt['predicate'],
                    'subject': mt['subject']
                } for mt in entry['mtriples']
            ]
            mtriples_dicts.extend(mtriple_dict)

            lex_dict = [
                {
                    'idx': entry['idx'],
                    'dataset': entry['dataset'],
                    'category': entry['category'],
                    'text': l['text'],
                    'comment': l['comment'],
                    'lid': l['lid']
                } for l in entry['lexes']
            ]
            lexes_dicts.extend(lex_dict)

        edf = pd.DataFrame(entries_dicts)
        odf = pd.DataFrame(otriples_dicts)
        mdf = pd.DataFrame(mtriples_dicts)
        ldf = pd.DataFrame(lexes_dicts)

        self._pandas = PANDAS_CONTAINER(edf=edf, odf=odf, mdf=mdf, ldf=ldf)

        return self._pandas

    def __len__(self):

        return len(self._db)

    def __str__(self):

        return self.release

    def __iter__(self):

        for entry in self._db:

            yield WebNLGEntry(entry)
