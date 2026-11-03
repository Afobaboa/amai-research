"""Initial recipe links work for fresh installs and preserve staff decisions."""
from django.test import TestCase

from . import models as lab
from .initial_data import ArchiveLoader


def recipe_sheet(name):
    return {'name': name, 'cells': {}, 'recipe': {
        'mixture_code': 'W', 'version_label': name, 'title': name,
        'groups': [], 'filling': [],
    }}


def bundle_data():
    card = {
        'identifier': 'W.1 (2)', 'title': 'Повторное испытание',
        'matches': [{'path': 'recipes.xlsx', 'sheet': 'W.1',
                     'status': 'confirmed'}],
        'runs': [{'scope': 'На бумаге', 'observations': [
            {'text': 'Исходное наблюдение'},
        ]}],
    }
    return {
        'mixtures': [{'code': 'W', 'title': 'Зимняя'}],
        'files': [
            {'path': 'tests.xlsx', 'sha256': 'b' * 64, 'title': 'Эксперименты',
             'sheets': [{'name': 'W.1 (2)', 'cells': {}, 'test': card}]},
            {'path': 'recipes.xlsx', 'sha256': 'a' * 64, 'title': 'Рецептуры',
             'sheets': [recipe_sheet('W.1'), recipe_sheet('W.2')]},
        ],
    }


class InitialRecipeLinkTests(TestCase):
    def setUp(self):
        self.author = lab.EmployeeProfile.objects.create(full_name='Автор')
        self.data = bundle_data()

    def load(self, status='confirmed'):
        self.data['files'][0]['sheets'][0]['test']['matches'][0]['status'] = status
        counts = ArchiveLoader(self.author).load(self.data)
        self.card = lab.TestSession.objects.get(source_sheet__name='W.1 (2)')
        self.recipe = lab.RecipeVersion.objects.get(version_label='W.1')
        return counts

    def counts(self):
        return tuple(model.objects.count() for model in (
            lab.TestSession, lab.RecipeVersion, lab.RecipeMatch, lab.TestRun,
            lab.Observation, lab.ChangeEvent,
        ))

    def choose_other_recipe(self):
        other = lab.RecipeVersion.objects.get(version_label='W.2')
        return lab.RecipeMatch.objects.create(
            session=self.card, recipe_version=other, status='confirmed',
        )

    def test_fresh_load_links_card_before_its_recipe_file_is_loaded(self):
        self.assertEqual(self.load()['tests'], 1)
        self.assertEqual(self.card.confirmed_recipe, self.recipe)
        self.assertEqual(self.card.identifier, 'W.1 (2)')

    def test_legacy_candidate_is_upgraded_without_reimporting_or_resetting_card(self):
        self.load('candidate')
        self.card.title = 'Название сотрудника'
        self.card.notes = 'Проверено сотрудником'
        self.card.save()
        observation = lab.Observation.objects.get()
        observation.text_value = 'Уточнённое наблюдение'
        observation.save()
        before = self.counts()
        self.assertEqual(self.load()['tests'], 0)
        self.load()
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.card.confirmed_recipe, self.recipe)
        self.assertEqual(self.card.title, 'Название сотрудника')
        self.assertEqual(self.card.notes, 'Проверено сотрудником')
        self.assertEqual(self.card.identifier, 'W.1 (2)')
        observation.refresh_from_db()
        self.assertEqual(observation.text_value, 'Уточнённое наблюдение')

    def test_missing_link_is_restored_for_existing_card(self):
        self.load()
        self.card.recipe_matches.all().delete()
        self.assertEqual(self.load()['tests'], 0)
        self.assertEqual(self.card.confirmed_recipe, self.recipe)
        self.assertEqual(self.card.recipe_matches.count(), 1)

    def test_rejected_link_is_preserved(self):
        self.load('rejected')
        self.load()
        self.assertEqual(self.card.recipe_matches.get().status, 'rejected')
        self.assertIsNone(self.card.confirmed_recipe)

    def test_manual_choice_blocks_upgrading_another_candidate(self):
        self.load('candidate')
        chosen = self.choose_other_recipe()
        self.load()
        self.assertEqual(self.card.confirmed_recipe, chosen.recipe_version)
        self.assertEqual(self.card.recipe_matches.get(
            recipe_version=self.recipe,
        ).status, 'candidate')

    def test_missing_link_does_not_replace_manual_choice(self):
        self.load()
        self.card.recipe_matches.all().delete()
        chosen = self.choose_other_recipe()
        self.load()
        before = self.counts()
        self.load()
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.card.confirmed_recipe, chosen.recipe_version)
        self.assertEqual(self.card.recipe_matches.get(
            recipe_version=self.recipe,
        ).status, 'candidate')

    def test_candidate_only_seed_does_not_demote_manual_confirmation(self):
        self.load()
        self.load('candidate')
        self.assertEqual(self.card.confirmed_recipe, self.recipe)

    def test_ambiguous_links_remain_candidates(self):
        matches = self.data['files'][0]['sheets'][0]['test']['matches']
        matches.append({'path': 'recipes.xlsx', 'sheet': 'W.2',
                        'status': 'candidate'})
        self.load('candidate')
        self.assertIsNone(self.card.confirmed_recipe)
        self.assertEqual(self.card.recipe_matches.count(), 2)
