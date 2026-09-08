import pytest
from pydantic import ValidationError

from tests.story_factories import make_story_article
from wechat_agent.story.models import (
    StoryCandidatePair,
    StoryRelation,
    StoryRelationType,
)


def test_candidate_pair_rejects_same_article():
    article = make_story_article(id="same")

    with pytest.raises(ValidationError):
        StoryCandidatePair.from_articles(article, article, now=article.collected_at)


def test_story_relation_rejects_self_reference():
    with pytest.raises(ValidationError):
        StoryRelation(
            story_id="story-1",
            related_story_id="story-1",
            relation_type=StoryRelationType.RELATED,
            created_at=make_story_article().collected_at,
        )
