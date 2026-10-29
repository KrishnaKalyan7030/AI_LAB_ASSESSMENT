"""Sample accounts, viva questions, assignments and submissions.

Sample submissions are run through the real test engine. The one sample viva
result is scored by the rule-based (non-AI) fallback and is labelled as such;
no AI output is fabricated.
"""
import json

from werkzeug.security import generate_password_hash

from .repository import create_submission, store_viva_result
from .services.fallbacks import viva_keyword_estimate

SAMPLE_USERS = [
    ("teacher1", "Teacher@123", "Dr. Asha Kulkarni", "teacher"),
    ("student1", "Student@123", "Rahul Patil", "student"),
    ("student2", "Student@123", "Sneha Deshmukh", "student"),
    ("student3", "Student@123", "Arjun Joshi", "student"),
]

SUBJECT = "Artificial Intelligence Laboratory"

VIVA_QUESTIONS = [
    {
        "experiment": "A* Search",
        "difficulty": "medium",
        "question": "What is a heuristic function in A* search, and why must it be admissible?",
        "reference_answer": (
            "A heuristic h(n) estimates the cost of the cheapest path from node n to the goal. "
            "A* expands nodes in order of f(n) = g(n) + h(n), where g(n) is the cost so far. "
            "The heuristic is admissible if it never overestimates the true remaining cost. "
            "With an admissible heuristic, A* (tree search) is guaranteed to return an optimal "
            "path; with a consistent heuristic the graph-search version is also optimal."
        ),
        "rubric": (
            "1 mark: defines h(n) as an estimate of remaining cost to the goal. "
            "1 mark: states f(n) = g(n) + h(n). "
            "1 mark: defines admissible as never overestimating the true cost. "
            "1 mark: links admissibility to optimality of A*. "
            "1 mark: example (e.g. straight-line distance or Manhattan distance) or mention of consistency."
        ),
        "key_concepts": "heuristic, estimate, g(n) + h(n), admissible, overestimate, optimal",
        "max_score": 5,
    },
    {
        "experiment": "Uninformed Search (BFS/DFS)",
        "difficulty": "easy",
        "question": "Compare breadth-first search and depth-first search in terms of data structure, completeness and optimality.",
        "reference_answer": (
            "BFS uses a FIFO queue and explores level by level; DFS uses a LIFO stack (or "
            "recursion) and explores one branch deeply. BFS is complete on finite branching "
            "factors and optimal when all step costs are equal. DFS is not complete in infinite "
            "or cyclic spaces without visited checks and is not optimal, but it uses much less "
            "memory: O(bm) versus O(b^d) for BFS."
        ),
        "rubric": (
            "1 mark: BFS uses a queue, DFS uses a stack/recursion. "
            "1 mark: BFS complete, DFS may not be. "
            "1 mark: BFS optimal for uniform step costs, DFS not optimal. "
            "1 mark: memory comparison (DFS uses less memory). "
            "1 mark: correct complexity or clear example."
        ),
        "key_concepts": "queue, stack, complete, optimal, memory",
        "max_score": 5,
    },
    {
        "experiment": "Minimax and Alpha-Beta Pruning",
        "difficulty": "medium",
        "question": "How does alpha-beta pruning improve minimax without changing its result?",
        "reference_answer": (
            "Alpha is the best value MAX can guarantee so far and beta is the best value MIN can "
            "guarantee. While searching, if a node's value shows alpha >= beta, the remaining "
            "children cannot affect the final decision, so they are pruned. The minimax value at "
            "the root is unchanged. With good move ordering the effective branching factor drops "
            "to about sqrt(b), letting the search go roughly twice as deep."
        ),
        "rubric": (
            "1 mark: defines alpha (MAX's best guaranteed). "
            "1 mark: defines beta (MIN's best guaranteed). "
            "1 mark: prune when alpha >= beta. "
            "1 mark: states the result is identical to minimax. "
            "1 mark: move ordering / O(b^(d/2)) benefit."
        ),
        "key_concepts": "alpha, beta, prune, same result, move ordering",
        "max_score": 5,
    },
    {
        "experiment": "Hill Climbing",
        "difficulty": "easy",
        "question": "What are local maxima, plateaus and ridges in hill climbing, and name one way to escape them?",
        "reference_answer": (
            "A local maximum is a state better than all neighbours but worse than the global "
            "maximum. A plateau is a flat region where neighbours have equal value, so there is no "
            "direction to move. A ridge is a sequence of local maxima that is hard to climb with "
            "single moves. Random-restart hill climbing, simulated annealing or allowing sideways "
            "moves help escape them."
        ),
        "rubric": (
            "1 mark: local maximum. 1 mark: plateau. 1 mark: ridge. "
            "1 mark: one valid escape technique. 1 mark: clear explanation of why greedy search gets stuck."
        ),
        "key_concepts": "local maximum, plateau, ridge, random restart, simulated annealing",
        "max_score": 5,
    },
    {
        "experiment": "Perceptron Learning",
        "difficulty": "hard",
        "question": "Explain the perceptron learning rule and why a single perceptron cannot learn XOR.",
        "reference_answer": (
            "For each training example the perceptron predicts y_hat = step(w.x + b). If wrong, "
            "weights are updated w <- w + eta (y - y_hat) x and b <- b + eta (y - y_hat). It "
            "converges only when the data is linearly separable. XOR is not linearly separable - "
            "no single straight line separates its classes - so a single perceptron cannot learn "
            "it; a multi-layer network with a hidden layer is required."
        ),
        "rubric": (
            "1 mark: prediction with weighted sum and step function. "
            "1 mark: correct weight update rule with learning rate. "
            "1 mark: converges for linearly separable data. "
            "1 mark: XOR not linearly separable. "
            "1 mark: hidden layer / multi-layer solution."
        ),
        "key_concepts": "weighted sum, learning rate, update, linearly separable, hidden layer",
        "max_score": 5,
    },
]

DRAFT_QUESTION = {
    "experiment": "Constraint Satisfaction",
    "difficulty": "medium",
    "question": "What is the minimum-remaining-values (MRV) heuristic in CSP backtracking?",
    "reference_answer": (
        "MRV chooses the unassigned variable with the fewest legal values left in its domain. "
        "It fails fast: a variable with no legal values is detected immediately, pruning the search."
    ),
    "rubric": "2 marks: definition. 2 marks: fail-first reasoning. 1 mark: example such as map colouring.",
    "key_concepts": "fewest legal values, fail first, prune",
    "max_score": 5,
}

BFS_DESCRIPTION = (
    "Write bfs_order(graph, start) that returns the order in which nodes are visited by "
    "breadth-first search. `graph` is a dictionary mapping each node to a list of neighbours; "
    "visit neighbours in the order they are listed and never visit a node twice."
)
BFS_STARTER = "def bfs_order(graph, start):\n    \"\"\"Return the BFS visiting order.\"\"\"\n    # your code here\n    return []\n"

ASSIGNMENTS = [
    {
        "title": "Breadth-First Search Traversal",
        "experiment": "Uninformed Search (BFS/DFS)",
        "description": BFS_DESCRIPTION,
        "function_name": "bfs_order",
        "starter_code": BFS_STARTER,
        "tests": [
            ("Simple tree", [{"A": ["B", "C"], "B": ["D"], "C": [], "D": []}, "A"], ["A", "B", "C", "D"], 0),
            ("Graph with a cycle", [{"1": ["2", "3"], "2": ["1", "4"], "3": ["4"], "4": []}, "1"], ["1", "2", "3", "4"], 0),
            ("Single node", [{"X": []}, "X"], ["X"], 0),
            ("Hidden: disconnected part", [{"S": ["T"], "T": [], "U": ["S"]}, "S"], ["S", "T"], 1),
        ],
    },
    {
        "title": "Manhattan Distance Heuristic",
        "experiment": "A* Search",
        "description": (
            "Write manhattan(a, b) returning the Manhattan distance between two grid cells "
            "given as [row, col] lists. This is the admissible heuristic used by A* on 4-way grids."
        ),
        "function_name": "manhattan",
        "starter_code": "def manhattan(a, b):\n    \"\"\"Manhattan distance between two [row, col] cells.\"\"\"\n    return 0\n",
        "tests": [
            ("Same cell", [[0, 0], [0, 0]], 0, 0),
            ("Diagonal", [[0, 0], [3, 4]], 7, 0),
            ("Negative direction", [[5, 2], [1, 6]], 8, 0),
            ("Hidden: large grid", [[100, 3], [7, 250]], 340, 1),
        ],
    },
    {
        "title": "Tic-Tac-Toe Winner (Minimax utility)",
        "experiment": "Minimax and Alpha-Beta Pruning",
        "description": (
            "Write winner(board) for a 3x3 board given as a list of 3 strings using 'X', 'O' and "
            "'.'. Return 'X' or 'O' if that player has three in a row (row, column or diagonal), "
            "otherwise return None. This is the terminal test used by minimax."
        ),
        "function_name": "winner",
        "starter_code": "def winner(board):\n    \"\"\"Return 'X', 'O' or None.\"\"\"\n    return None\n",
        "tests": [
            ("Row win", [["XXX", "OO.", "..."]], "X", 0),
            ("Column win", [["OX.", "OX.", "O.X"]], "O", 0),
            ("No winner", [["XOX", "XOO", "OXX"]], None, 0),
            ("Hidden: anti-diagonal", [["..O", ".O.", "OXX"]], "O", 1),
        ],
    },
]

STUDENT1_BFS = '''from collections import deque


def bfs_order(graph, start):
    """Return the order in which BFS visits the nodes."""
    visited = [start]
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for neighbour in graph.get(node, []):
            if neighbour not in visited:
                visited.append(neighbour)
                queue.append(neighbour)
    return visited
'''

# Same structure with renamed variables - used to demonstrate similarity detection.
STUDENT2_BFS = '''from collections import deque

def bfs_order(g, s):
    seen = [s]
    q = deque([s])
    while q:
        cur = q.popleft()
        for nxt in g.get(cur, []):
            if nxt not in seen:
                seen.append(nxt)
                q.append(nxt)
    return seen
'''

# A DFS-style attempt that fails some BFS tests.
STUDENT3_BFS = '''def bfs_order(graph, start):
    order = []
    stack = [start]
    while stack:
        node = stack.pop()
        if node not in order:
            order.append(node)
            for n in graph[node]:
                stack.append(n)
    return order
'''

STUDENT1_MANHATTAN = '''def manhattan(a, b):
    """Manhattan distance between two [row, col] cells."""
    return abs(a[0] - b[0]) + abs(a[1] - b[1])
'''

STUDENT1_VIVA_ANSWER = (
    "A heuristic is an estimate of the cost from the current node to the goal. A* uses "
    "f(n) = g(n) + h(n). It should be admissible, meaning it does not overestimate, so that "
    "the path found is optimal."
)


def seed_database(db) -> None:
    ids = {}
    for username, password, name, role in SAMPLE_USERS:
        cur = db.execute(
            "INSERT INTO users (username, password_hash, full_name, role) VALUES (?, ?, ?, ?)",
            (username, generate_password_hash(password), name, role),
        )
        ids[username] = cur.lastrowid
    teacher = ids["teacher1"]

    question_ids = []
    for q in VIVA_QUESTIONS:
        cur = db.execute(
            "INSERT INTO viva_questions (subject, experiment, difficulty, question, reference_answer, "
            "rubric, key_concepts, max_score, status, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'published', ?)",
            (SUBJECT, q["experiment"], q["difficulty"], q["question"], q["reference_answer"],
             q["rubric"], q["key_concepts"], q["max_score"], teacher),
        )
        question_ids.append(cur.lastrowid)
    q = DRAFT_QUESTION
    db.execute(
        "INSERT INTO viva_questions (subject, experiment, difficulty, question, reference_answer, "
        "rubric, key_concepts, max_score, status, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?)",
        (SUBJECT, q["experiment"], q["difficulty"], q["question"], q["reference_answer"],
         q["rubric"], q["key_concepts"], q["max_score"], teacher),
    )

    assignment_rows = []
    for a in ASSIGNMENTS:
        cur = db.execute(
            "INSERT INTO assignments (title, experiment, description, function_name, starter_code, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (a["title"], a["experiment"], a["description"], a["function_name"], a["starter_code"], teacher),
        )
        aid = cur.lastrowid
        for desc, args, expected, hidden in a["tests"]:
            db.execute(
                "INSERT INTO test_cases (assignment_id, description, args_json, expected_json, is_hidden) "
                "VALUES (?, ?, ?, ?, ?)",
                (aid, desc, json.dumps(args), json.dumps(expected), hidden),
            )
        assignment_rows.append(db.execute("SELECT * FROM assignments WHERE id = ?", (aid,)).fetchone())
    db.commit()

    bfs, manhattan_asg = assignment_rows[0], assignment_rows[1]
    create_submission(db, bfs, ids["student1"], STUDENT1_BFS, timeout=5)
    create_submission(db, bfs, ids["student2"], STUDENT2_BFS, timeout=5)
    create_submission(db, bfs, ids["student3"], STUDENT3_BFS, timeout=5)
    create_submission(db, manhattan_asg, ids["student1"], STUDENT1_MANHATTAN, timeout=5)

    # One sample viva result, honestly scored by the non-AI keyword fallback.
    question = db.execute("SELECT * FROM viva_questions WHERE id = ?", (question_ids[0],)).fetchone()
    cur = db.execute(
        "INSERT INTO viva_attempts (student_id, question_id, answer) VALUES (?, ?, ?)",
        (ids["student1"], question["id"], STUDENT1_VIVA_ANSWER),
    )
    attempt = db.execute("SELECT * FROM viva_attempts WHERE id = ?", (cur.lastrowid,)).fetchone()
    store_viva_result(db, attempt, viva_keyword_estimate(question, STUDENT1_VIVA_ANSWER), "rule_based", None)
    db.commit()
