"""Token-based similarity detection (no AI)."""
from app.services.similarity import HUMAN_REVIEW_NOTE, compare, normalize_tokens

BFS_A = '''from collections import deque

def bfs(graph, start):
    """BFS order."""
    visited = [start]
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for nb in graph[node]:
            if nb not in visited:
                visited.append(nb)
                queue.append(nb)
    return visited
'''

# Renamed identifiers, different comments and spacing - same structure.
BFS_B = '''from collections import deque
# my own work, honest
def bfs(g,   s):
    seen=[s]
    q=deque([s])
    while q:
        cur=q.popleft()
        for x in g[cur]:
            if x not in seen:
                seen.append(x)
                q.append(x)
    return seen
'''

DIFFERENT = '''def bfs(graph, start):
    order, frontier = [], [start]
    while frontier:
        nxt = []
        for n in frontier:
            if n in order:
                continue
            order.append(n)
            nxt.extend(graph.get(n, []))
        frontier = nxt
    return order
'''


def test_renamed_copy_is_flagged():
    result = compare(BFS_A, BFS_B, threshold=0.75)
    assert result.percent >= 90
    assert result.flagged
    assert result.matching_sections
    section = result.matching_sections[0]
    assert section["a_lines"][0] >= 1 and "popleft" in section["a_excerpt"]


def test_different_implementation_not_flagged():
    result = compare(BFS_A, DIFFERENT, threshold=0.75)
    assert result.percent < 75 and not result.flagged


def test_identical_is_100_percent():
    assert compare(BFS_A, BFS_A).percent == 100


def test_comments_and_whitespace_ignored():
    assert [t.kind for t in normalize_tokens("x = 1  # hi\n")] == \
           [t.kind for t in normalize_tokens("y=2\n")]


def test_unparseable_code_still_compared():
    result = compare("def f(:\n  return 1", "def g(:\n  return 2")
    assert 0 <= result.similarity <= 1


def test_empty_code():
    assert compare("", BFS_A).similarity == 0


def test_teacher_runs_similarity_and_sees_warning(teacher, query):
    resp = teacher.post("/teacher/assignments/1/similarity")
    assert resp.status_code == 302
    rows = query("SELECT * FROM similarity_comparisons WHERE assignment_id = 1 ORDER BY similarity DESC")
    assert len(rows) == 3  # 3 students -> 3 pairs
    top = rows[0]
    pair = {top["submission_a"], top["submission_b"]}
    assert pair == {1, 2}  # seeded student1 / student2 renamed copies
    assert top["similarity"] >= 0.75
    page = teacher.get("/teacher/assignments/1").data.decode()
    assert "require human review" in page
    detail = teacher.get(f"/teacher/similarity/{top['id']}").data.decode()
    assert "appear similar" in detail and "not</strong> proof of plagiarism" in detail
    assert "Matching sections" in detail


def test_similarity_does_not_call_claude(teacher, fake_claude):
    teacher.post("/teacher/assignments/1/similarity")
    assert fake_claude.calls == []


def test_review_note_present():
    assert "human review" in HUMAN_REVIEW_NOTE
