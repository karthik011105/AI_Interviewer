"""One-shot script to append 200+ new questions to the assessment question bank.

Run from the repo root:
    python scripts/expand_question_bank.py
"""

from __future__ import annotations
import json
from pathlib import Path

BANK_PATH = Path("backend/data/assessments/question_bank.json")

# ---------------------------------------------------------------------------
# New questions to append
# ---------------------------------------------------------------------------
NEW_QUESTIONS: list[dict] = [

# ===========================================================================
# HARD CORE QUESTIONS (universal, picked for every role)
# ===========================================================================
{
    "question_id": "core_deadlock_01",
    "prompt": "Which four conditions must ALL hold simultaneously for a deadlock to occur?",
    "options": [
        {"id": "a", "text": "Mutual exclusion, hold-and-wait, no preemption, circular wait"},
        {"id": "b", "text": "Starvation, livelock, context switching, page faults"},
        {"id": "c", "text": "Synchronisation, parallelism, busy-waiting, spinlock"},
        {"id": "d", "text": "Caching, pipelining, branching, memory mapping"}
    ],
    "correct_option_id": "a",
    "explanation": "Coffman's four necessary conditions for deadlock are mutual exclusion, hold-and-wait, no preemption, and circular wait.",
    "topic": "operating_systems",
    "difficulty": "hard",
    "skill_tag": "deadlock_conditions",
    "question_type": "mcq_single",
    "is_core": True,
    "active": True
},
{
    "question_id": "core_acid_01",
    "prompt": "In database transactions, what does the 'I' in ACID stand for and what does it guarantee?",
    "options": [
        {"id": "a", "text": "Isolation — concurrent transactions do not see each other's uncommitted changes"},
        {"id": "b", "text": "Integrity — only integers can be stored"},
        {"id": "c", "text": "Immutability — rows cannot be updated after insert"},
        {"id": "d", "text": "Indexing — all queries use an index"}
    ],
    "correct_option_id": "a",
    "explanation": "Isolation ensures that concurrent transactions execute as if serialised, preventing dirty reads and phantoms.",
    "topic": "dbms",
    "difficulty": "hard",
    "skill_tag": "acid_isolation",
    "question_type": "mcq_single",
    "is_core": True,
    "active": True
},
{
    "question_id": "core_big_o_hard_01",
    "prompt": "An algorithm has two nested loops over n items plus a final O(n log n) sort. What is its overall time complexity?",
    "options": [
        {"id": "a", "text": "O(n^2) because nested loops dominate"},
        {"id": "b", "text": "O(n log n) because the sort dominates"},
        {"id": "c", "text": "O(n^3)"},
        {"id": "d", "text": "O(log n)"}
    ],
    "correct_option_id": "a",
    "explanation": "O(n^2) from nested loops dominates O(n log n), so the overall complexity is O(n^2).",
    "topic": "logic",
    "difficulty": "hard",
    "skill_tag": "complexity_analysis",
    "question_type": "mcq_single",
    "is_core": True,
    "active": True
},
{
    "question_id": "core_hash_collision_01",
    "prompt": "Which technique handles hash collisions by storing all colliding keys in a linked list at the same bucket?",
    "options": [
        {"id": "a", "text": "Separate chaining"},
        {"id": "b", "text": "Linear probing"},
        {"id": "c", "text": "Double hashing"},
        {"id": "d", "text": "Robin Hood hashing"}
    ],
    "correct_option_id": "a",
    "explanation": "Separate chaining maintains a list (or another structure) per bucket for colliding entries.",
    "topic": "data_structures",
    "difficulty": "hard",
    "skill_tag": "hash_collision",
    "question_type": "mcq_single",
    "is_core": True,
    "active": True
},
{
    "question_id": "core_virtual_memory_01",
    "prompt": "What problem does virtual memory primarily solve?",
    "options": [
        {"id": "a", "text": "Allowing processes to use more memory than physically available and providing isolation"},
        {"id": "b", "text": "Making disks faster"},
        {"id": "c", "text": "Eliminating the need for a CPU cache"},
        {"id": "d", "text": "Replacing the file system"}
    ],
    "correct_option_id": "a",
    "explanation": "Virtual memory abstracts physical RAM, enables isolation between processes, and allows demand paging.",
    "topic": "operating_systems",
    "difficulty": "hard",
    "skill_tag": "virtual_memory",
    "question_type": "mcq_single",
    "is_core": True,
    "active": True
},

# ===========================================================================
# CODE OUTPUT QUESTIONS — Python
# ===========================================================================
{
    "question_id": "code_py_list_mul_01",
    "prompt": "What does the following Python code print?\n\nx = [0] * 3\nx[1] = 9\nprint(x)",
    "options": [
        {"id": "a", "text": "[0, 9, 0]"},
        {"id": "b", "text": "[9, 9, 9]"},
        {"id": "c", "text": "[0, 0, 0]"},
        {"id": "d", "text": "Error"}
    ],
    "correct_option_id": "a",
    "explanation": "[0]*3 creates [0,0,0]. Assigning 9 to index 1 gives [0,9,0]. Integer multiplication of lists produces independent slots.",
    "role_families": ["backend", "data", "ai_ml", "software_foundations"],
    "domains": ["software", "data", "ai_ml"],
    "topic": "python",
    "difficulty": "medium",
    "skill_tag": "list_replication",
    "question_type": "code_output",
    "is_core": False,
    "active": True
},
{
    "question_id": "code_py_mutable_default_01",
    "prompt": "What does this Python code print?\n\ndef add(item, lst=[]):\n    lst.append(item)\n    return lst\n\nprint(add(1))\nprint(add(2))",
    "options": [
        {"id": "a", "text": "[1]\n[1, 2]"},
        {"id": "b", "text": "[1]\n[2]"},
        {"id": "c", "text": "[1]\n[2, 1]"},
        {"id": "d", "text": "Error"}
    ],
    "correct_option_id": "a",
    "explanation": "The default list [] is created once at function definition time and shared across calls, so the second call sees the first call's item.",
    "role_families": ["backend", "data", "ai_ml"],
    "domains": ["software", "data"],
    "topic": "python",
    "difficulty": "hard",
    "skill_tag": "mutable_defaults",
    "question_type": "code_output",
    "is_core": False,
    "active": True
},
{
    "question_id": "code_py_slicing_01",
    "prompt": "What is the output of the following Python snippet?\n\nnums = [1, 2, 3, 4, 5]\nprint(nums[1:4])",
    "options": [
        {"id": "a", "text": "[2, 3, 4]"},
        {"id": "b", "text": "[1, 2, 3, 4]"},
        {"id": "c", "text": "[2, 3, 4, 5]"},
        {"id": "d", "text": "[1, 2, 3]"}
    ],
    "correct_option_id": "a",
    "explanation": "Python slices are start-inclusive and end-exclusive. nums[1:4] returns elements at indices 1, 2, 3.",
    "role_families": ["backend", "data", "ai_ml", "software_foundations"],
    "domains": ["software", "data"],
    "topic": "python",
    "difficulty": "easy",
    "skill_tag": "list_slicing",
    "question_type": "code_output",
    "is_core": False,
    "active": True
},
{
    "question_id": "code_py_generator_01",
    "prompt": "What does this Python code print?\n\ngen = (x*x for x in range(3))\nprint(list(gen))",
    "options": [
        {"id": "a", "text": "[0, 1, 4]"},
        {"id": "b", "text": "[1, 4, 9]"},
        {"id": "c", "text": "(0, 1, 4)"},
        {"id": "d", "text": "[0, 1, 2]"}
    ],
    "correct_option_id": "a",
    "explanation": "range(3) produces 0, 1, 2. Squaring gives 0, 1, 4. Wrapping in list() materialises the generator.",
    "role_families": ["backend", "data", "ai_ml"],
    "domains": ["software", "data"],
    "topic": "python",
    "difficulty": "medium",
    "skill_tag": "generator_expression",
    "question_type": "code_output",
    "is_core": False,
    "active": True
},
{
    "question_id": "code_py_dict_get_01",
    "prompt": "What does this code print?\n\nd = {'a': 1, 'b': 2}\nprint(d.get('c', 99))",
    "options": [
        {"id": "a", "text": "99"},
        {"id": "b", "text": "None"},
        {"id": "c", "text": "KeyError"},
        {"id": "d", "text": "0"}
    ],
    "correct_option_id": "a",
    "explanation": "dict.get(key, default) returns the default when the key is not present instead of raising KeyError.",
    "role_families": ["backend", "data", "ai_ml", "software_foundations"],
    "domains": ["software", "data"],
    "topic": "python",
    "difficulty": "easy",
    "skill_tag": "dict_get",
    "question_type": "code_output",
    "is_core": False,
    "active": True
},
{
    "question_id": "code_py_lambda_map_01",
    "prompt": "What does this Python code print?\n\nresult = list(map(lambda x: x + 10, [1, 2, 3]))\nprint(result)",
    "options": [
        {"id": "a", "text": "[11, 12, 13]"},
        {"id": "b", "text": "[1, 2, 3, 10]"},
        {"id": "c", "text": "[10, 20, 30]"},
        {"id": "d", "text": "Error"}
    ],
    "correct_option_id": "a",
    "explanation": "map applies the lambda to each element. 1+10=11, 2+10=12, 3+10=13.",
    "role_families": ["backend", "data", "ai_ml"],
    "domains": ["software"],
    "topic": "python",
    "difficulty": "medium",
    "skill_tag": "map_lambda",
    "question_type": "code_output",
    "is_core": False,
    "active": True
},

# ===========================================================================
# CODE OUTPUT QUESTIONS — JavaScript
# ===========================================================================
{
    "question_id": "code_js_typeof_01",
    "prompt": "What does this JavaScript expression evaluate to?\n\ntypeof null",
    "options": [
        {"id": "a", "text": "\"object\""},
        {"id": "b", "text": "\"null\""},
        {"id": "c", "text": "\"undefined\""},
        {"id": "d", "text": "\"boolean\""}
    ],
    "correct_option_id": "a",
    "explanation": "typeof null returns 'object' — a well-known legacy bug in JavaScript that was never corrected.",
    "role_families": ["frontend", "backend", "mobile"],
    "domains": ["software"],
    "topic": "javascript",
    "difficulty": "medium",
    "skill_tag": "typeof_null",
    "question_type": "code_output",
    "is_core": False,
    "active": True
},
{
    "question_id": "code_js_closure_01",
    "prompt": "What is printed by this JavaScript code?\n\nfunction counter() {\n  let count = 0;\n  return function() { count++; return count; };\n}\nconst inc = counter();\nconsole.log(inc());\nconsole.log(inc());",
    "options": [
        {"id": "a", "text": "1\n2"},
        {"id": "b", "text": "0\n1"},
        {"id": "c", "text": "1\n1"},
        {"id": "d", "text": "undefined\nundefined"}
    ],
    "correct_option_id": "a",
    "explanation": "The inner function closes over count. Each call increments it from its previous value, giving 1 then 2.",
    "role_families": ["frontend", "backend"],
    "domains": ["software"],
    "topic": "javascript",
    "difficulty": "hard",
    "skill_tag": "closure",
    "question_type": "code_output",
    "is_core": False,
    "active": True
},
{
    "question_id": "code_js_promise_01",
    "prompt": "What is the order of console output for this code?\n\nconsole.log('A');\nPromise.resolve().then(() => console.log('B'));\nconsole.log('C');",
    "options": [
        {"id": "a", "text": "A\nC\nB"},
        {"id": "b", "text": "A\nB\nC"},
        {"id": "c", "text": "B\nA\nC"},
        {"id": "d", "text": "C\nA\nB"}
    ],
    "correct_option_id": "a",
    "explanation": "Synchronous code runs first (A, C), then microtasks (Promise .then) run before the next macrotask (B).",
    "role_families": ["frontend", "backend"],
    "domains": ["software"],
    "topic": "javascript",
    "difficulty": "hard",
    "skill_tag": "event_loop_microtask",
    "question_type": "code_output",
    "is_core": False,
    "active": True
},
{
    "question_id": "code_js_array_filter_01",
    "prompt": "What does this code log?\n\nconst nums = [1, 2, 3, 4, 5];\nconsole.log(nums.filter(n => n % 2 === 0));",
    "options": [
        {"id": "a", "text": "[2, 4]"},
        {"id": "b", "text": "[1, 3, 5]"},
        {"id": "c", "text": "[2, 4, 6]"},
        {"id": "d", "text": "2 4"}
    ],
    "correct_option_id": "a",
    "explanation": "filter keeps elements for which the predicate returns true. 2 and 4 are even.",
    "role_families": ["frontend", "backend", "mobile"],
    "domains": ["software"],
    "topic": "javascript",
    "difficulty": "easy",
    "skill_tag": "array_filter",
    "question_type": "code_output",
    "is_core": False,
    "active": True
},

# ===========================================================================
# CODE OUTPUT QUESTIONS — Java
# ===========================================================================
{
    "question_id": "code_java_string_eq_01",
    "prompt": "What does this Java code print?\n\nString a = new String(\"hello\");\nString b = new String(\"hello\");\nSystem.out.println(a == b);\nSystem.out.println(a.equals(b));",
    "options": [
        {"id": "a", "text": "false\ntrue"},
        {"id": "b", "text": "true\ntrue"},
        {"id": "c", "text": "false\nfalse"},
        {"id": "d", "text": "true\nfalse"}
    ],
    "correct_option_id": "a",
    "explanation": "== compares references; two new String() objects are different references. .equals() compares character content.",
    "role_keys": ["backend_java_developer"],
    "role_families": ["backend"],
    "domains": ["software"],
    "topic": "java",
    "difficulty": "medium",
    "skill_tag": "string_equality",
    "question_type": "code_output",
    "is_core": False,
    "active": True
},
{
    "question_id": "code_java_static_01",
    "prompt": "What does this Java snippet print?\n\nclass Counter {\n  static int count = 0;\n  Counter() { count++; }\n}\npublic class Main {\n  public static void main(String[] a) {\n    new Counter(); new Counter();\n    System.out.println(Counter.count);\n  }\n}",
    "options": [
        {"id": "a", "text": "2"},
        {"id": "b", "text": "0"},
        {"id": "c", "text": "1"},
        {"id": "d", "text": "Error"}
    ],
    "correct_option_id": "a",
    "explanation": "static fields are shared across all instances. Each constructor call increments the shared count, giving 2.",
    "role_keys": ["backend_java_developer"],
    "role_families": ["backend"],
    "domains": ["software"],
    "topic": "java",
    "difficulty": "medium",
    "skill_tag": "static_fields",
    "question_type": "code_output",
    "is_core": False,
    "active": True
},

# ===========================================================================
# CODE OUTPUT QUESTIONS — OOP / General programming
# ===========================================================================
{
    "question_id": "code_oops_polymorphism_01",
    "prompt": "What concept is demonstrated when a subclass overrides a parent method and the program selects the correct implementation at runtime?",
    "options": [
        {"id": "a", "text": "Runtime polymorphism (method overriding)"},
        {"id": "b", "text": "Compile-time polymorphism (method overloading)"},
        {"id": "c", "text": "Encapsulation"},
        {"id": "d", "text": "Abstraction"}
    ],
    "correct_option_id": "a",
    "explanation": "Runtime polymorphism dispatches method calls based on the actual object type, not the declared type.",
    "role_families": ["backend", "software_foundations", "mobile"],
    "domains": ["software"],
    "topic": "oops",
    "difficulty": "medium",
    "skill_tag": "runtime_polymorphism",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "code_oops_abstract_01",
    "prompt": "What distinguishes an abstract class from an interface in most languages?",
    "options": [
        {"id": "a", "text": "An abstract class can have state and concrete methods; interfaces traditionally only define contracts"},
        {"id": "b", "text": "An abstract class cannot be extended"},
        {"id": "c", "text": "An interface can only hold primitive types"},
        {"id": "d", "text": "They are identical in all modern languages"}
    ],
    "correct_option_id": "a",
    "explanation": "Abstract classes can hold fields and implemented methods. Interfaces (traditionally) only declare method signatures.",
    "role_families": ["backend", "software_foundations"],
    "domains": ["software"],
    "topic": "oops",
    "difficulty": "medium",
    "skill_tag": "abstract_vs_interface",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "oops_solid_srp_01",
    "prompt": "The Single Responsibility Principle (SRP) states that a class should:",
    "options": [
        {"id": "a", "text": "Have only one reason to change"},
        {"id": "b", "text": "Inherit from at most one parent"},
        {"id": "c", "text": "Never use interfaces"},
        {"id": "d", "text": "Hold no more than ten methods"}
    ],
    "correct_option_id": "a",
    "explanation": "SRP says a class should have a single responsibility, meaning only one axis of change.",
    "role_families": ["backend", "software_foundations"],
    "domains": ["software"],
    "topic": "oops",
    "difficulty": "medium",
    "skill_tag": "solid_srp",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "oops_design_singleton_01",
    "prompt": "What design pattern ensures only one instance of a class exists across the entire application?",
    "options": [
        {"id": "a", "text": "Singleton"},
        {"id": "b", "text": "Factory"},
        {"id": "c", "text": "Observer"},
        {"id": "d", "text": "Decorator"}
    ],
    "correct_option_id": "a",
    "explanation": "The Singleton pattern restricts instantiation to a single object, often used for config or connection managers.",
    "role_families": ["backend", "software_foundations"],
    "domains": ["software"],
    "topic": "oops",
    "difficulty": "easy",
    "skill_tag": "singleton_pattern",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},

# ===========================================================================
# PROGRAMMING / LOGIC — additional questions
# ===========================================================================
{
    "question_id": "code_logic_recursion_01",
    "prompt": "What does this Python function return when called as f(4)?\n\ndef f(n):\n    if n <= 1:\n        return 1\n    return n * f(n - 1)",
    "options": [
        {"id": "a", "text": "24"},
        {"id": "b", "text": "4"},
        {"id": "c", "text": "16"},
        {"id": "d", "text": "120"}
    ],
    "correct_option_id": "a",
    "explanation": "f is factorial. f(4) = 4*3*2*1 = 24.",
    "role_families": ["software_foundations", "backend", "data", "ai_ml"],
    "domains": ["software", "data"],
    "topic": "logic",
    "difficulty": "medium",
    "skill_tag": "recursion_factorial",
    "question_type": "code_output",
    "is_core": False,
    "active": True
},
{
    "question_id": "logic_two_pointer_01",
    "prompt": "A two-pointer technique on a sorted array typically reduces the time complexity of finding a pair that sums to a target from:",
    "options": [
        {"id": "a", "text": "O(n^2) to O(n)"},
        {"id": "b", "text": "O(n) to O(log n)"},
        {"id": "c", "text": "O(n log n) to O(1)"},
        {"id": "d", "text": "O(n^3) to O(n^2)"}
    ],
    "correct_option_id": "a",
    "explanation": "Brute-force pair search is O(n^2). Two pointers scan from both ends in a single pass: O(n).",
    "role_families": ["software_foundations", "backend", "data", "ai_ml"],
    "domains": ["software"],
    "topic": "logic",
    "difficulty": "hard",
    "skill_tag": "two_pointer",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "logic_stack_balancing_01",
    "prompt": "Which data structure is best suited for checking if brackets in a string are balanced?",
    "options": [
        {"id": "a", "text": "Stack"},
        {"id": "b", "text": "Queue"},
        {"id": "c", "text": "Heap"},
        {"id": "d", "text": "Graph"}
    ],
    "correct_option_id": "a",
    "explanation": "A stack lets you push opening brackets and pop when a matching closing bracket appears, naturally validating nesting.",
    "role_families": ["software_foundations", "backend", "data"],
    "domains": ["software"],
    "topic": "logic",
    "difficulty": "easy",
    "skill_tag": "stack_bracket_matching",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "logic_dp_intro_01",
    "prompt": "Dynamic programming solves complex problems by:",
    "options": [
        {"id": "a", "text": "Breaking them into overlapping subproblems and caching results to avoid recomputation"},
        {"id": "b", "text": "Running the algorithm on multiple threads simultaneously"},
        {"id": "c", "text": "Choosing the locally optimal step at each point"},
        {"id": "d", "text": "Sorting the input before applying recursion"}
    ],
    "correct_option_id": "a",
    "explanation": "DP memoises (or tabulates) solutions to subproblems so they are computed only once.",
    "role_families": ["software_foundations", "backend", "data", "ai_ml"],
    "domains": ["software"],
    "topic": "logic",
    "difficulty": "medium",
    "skill_tag": "dynamic_programming",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},

# ===========================================================================
# DATA STRUCTURES — additional
# ===========================================================================
{
    "question_id": "ds_stack_push_pop_01",
    "prompt": "After pushing 1, 2, 3 onto a stack and then popping twice, what is on top?",
    "options": [
        {"id": "a", "text": "1"},
        {"id": "b", "text": "2"},
        {"id": "c", "text": "3"},
        {"id": "d", "text": "Empty"}
    ],
    "correct_option_id": "a",
    "explanation": "3 is popped first, then 2, leaving 1 on top.",
    "role_families": ["software_foundations", "backend", "data"],
    "domains": ["software"],
    "topic": "data_structures",
    "difficulty": "easy",
    "skill_tag": "stack_operations",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "ds_linked_list_01",
    "prompt": "What is the time complexity of searching for an element in an unsorted singly linked list?",
    "options": [
        {"id": "a", "text": "O(n)"},
        {"id": "b", "text": "O(1)"},
        {"id": "c", "text": "O(log n)"},
        {"id": "d", "text": "O(n^2)"}
    ],
    "correct_option_id": "a",
    "explanation": "You may need to traverse the entire list to find an element, giving linear time.",
    "role_families": ["software_foundations", "backend", "data"],
    "domains": ["software"],
    "topic": "data_structures",
    "difficulty": "easy",
    "skill_tag": "linked_list_search",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "ds_bst_property_01",
    "prompt": "In a valid Binary Search Tree, for any node N, which statement is true?",
    "options": [
        {"id": "a", "text": "All values in the left subtree are less than N, and all values in the right subtree are greater"},
        {"id": "b", "text": "The left child is always larger than the right child"},
        {"id": "c", "text": "All leaf nodes are at the same level"},
        {"id": "d", "text": "Nodes have at most one child"}
    ],
    "correct_option_id": "a",
    "explanation": "BST ordering property: left < node < right, maintained recursively.",
    "role_families": ["software_foundations", "backend", "data"],
    "domains": ["software"],
    "topic": "data_structures",
    "difficulty": "medium",
    "skill_tag": "bst_property",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "ds_heap_min_01",
    "prompt": "A min-heap data structure always ensures that:",
    "options": [
        {"id": "a", "text": "The smallest element is at the root"},
        {"id": "b", "text": "All elements are sorted in ascending order"},
        {"id": "c", "text": "The largest element is at the root"},
        {"id": "d", "text": "Left and right subtrees are always equal in size"}
    ],
    "correct_option_id": "a",
    "explanation": "In a min-heap, the root is always the minimum. The heap property is maintained after each insert or delete.",
    "role_families": ["software_foundations", "backend", "data"],
    "domains": ["software"],
    "topic": "data_structures",
    "difficulty": "medium",
    "skill_tag": "min_heap",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "ds_graph_bfs_01",
    "prompt": "Breadth-First Search (BFS) on a graph is typically used to find:",
    "options": [
        {"id": "a", "text": "The shortest path in an unweighted graph"},
        {"id": "b", "text": "The longest path in any graph"},
        {"id": "c", "text": "All negative-weight cycles"},
        {"id": "d", "text": "The minimum spanning tree"}
    ],
    "correct_option_id": "a",
    "explanation": "BFS explores level by level, guaranteeing the shortest path (by edge count) in an unweighted graph.",
    "role_families": ["software_foundations", "backend", "data", "ai_ml"],
    "domains": ["software"],
    "topic": "data_structures",
    "difficulty": "medium",
    "skill_tag": "bfs_shortest_path",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},

# ===========================================================================
# TOOLS / GIT — additional
# ===========================================================================
{
    "question_id": "tools_git_rebase_01",
    "prompt": "What is the main difference between git merge and git rebase?",
    "options": [
        {"id": "a", "text": "Rebase rewrites commit history for a linear log; merge preserves history with a merge commit"},
        {"id": "b", "text": "Merge deletes the feature branch; rebase keeps it"},
        {"id": "c", "text": "Rebase can only be used on the main branch"},
        {"id": "d", "text": "They are identical in all outcomes"}
    ],
    "correct_option_id": "a",
    "explanation": "git rebase replays commits on top of another branch creating a linear history; git merge creates a merge commit.",
    "role_families": ["software_foundations", "backend", "frontend", "devops", "data", "ai_ml"],
    "domains": ["software", "devops"],
    "topic": "tools",
    "difficulty": "medium",
    "skill_tag": "git_rebase_vs_merge",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "tools_git_conflict_01",
    "prompt": "When a merge conflict occurs in git, what must you do before the merge can be completed?",
    "options": [
        {"id": "a", "text": "Manually edit the conflicting files, stage them, and then commit"},
        {"id": "b", "text": "Delete the conflicting files and re-add them"},
        {"id": "c", "text": "Force push to origin to overwrite"},
        {"id": "d", "text": "Restart git from scratch"}
    ],
    "correct_option_id": "a",
    "explanation": "Conflict markers must be resolved manually; after editing the file, git add it and then run git commit.",
    "role_families": ["software_foundations", "backend", "frontend", "devops"],
    "domains": ["software", "devops"],
    "topic": "tools",
    "difficulty": "easy",
    "skill_tag": "merge_conflict_resolution",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "tools_docker_image_01",
    "prompt": "What is the difference between a Docker image and a Docker container?",
    "options": [
        {"id": "a", "text": "An image is a read-only blueprint; a container is a running instance of that image"},
        {"id": "b", "text": "An image contains running processes; a container is stored on disk only"},
        {"id": "c", "text": "They are the same thing"},
        {"id": "d", "text": "A container holds multiple images"}
    ],
    "correct_option_id": "a",
    "explanation": "Images are immutable templates; containers are writable, running instances created from images.",
    "role_families": ["backend", "devops", "data", "ai_ml"],
    "domains": ["software", "devops"],
    "topic": "tools",
    "difficulty": "easy",
    "skill_tag": "docker_image_vs_container",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},

# ===========================================================================
# SQL — additional
# ===========================================================================
{
    "question_id": "sql_group_by_01",
    "prompt": "What does GROUP BY do in an SQL query?",
    "options": [
        {"id": "a", "text": "Aggregates rows with the same value in the specified column(s)"},
        {"id": "b", "text": "Sorts the results in descending order"},
        {"id": "c", "text": "Filters rows before aggregation"},
        {"id": "d", "text": "Joins two tables on a common key"}
    ],
    "correct_option_id": "a",
    "explanation": "GROUP BY collapses rows with identical values into one group so aggregate functions (SUM, COUNT, etc.) apply per group.",
    "role_families": ["backend", "data"],
    "domains": ["software", "data"],
    "topic": "sql",
    "difficulty": "easy",
    "skill_tag": "sql_group_by",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "sql_having_01",
    "prompt": "Which clause is used to filter groups after GROUP BY, as opposed to filtering individual rows?",
    "options": [
        {"id": "a", "text": "HAVING"},
        {"id": "b", "text": "WHERE"},
        {"id": "c", "text": "LIMIT"},
        {"id": "d", "text": "JOIN"}
    ],
    "correct_option_id": "a",
    "explanation": "HAVING filters aggregated groups. WHERE filters rows before grouping.",
    "role_families": ["backend", "data"],
    "domains": ["software", "data"],
    "topic": "sql",
    "difficulty": "medium",
    "skill_tag": "sql_having",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "sql_subquery_01",
    "prompt": "In SQL, a correlated subquery:",
    "options": [
        {"id": "a", "text": "Refers to a column from the outer query and is re-evaluated for each outer row"},
        {"id": "b", "text": "Is computed once and its result is reused for every outer row"},
        {"id": "c", "text": "Cannot use aggregate functions"},
        {"id": "d", "text": "Must always return a single row"}
    ],
    "correct_option_id": "a",
    "explanation": "A correlated subquery depends on the outer query's current row, so it runs once per outer row.",
    "role_families": ["backend", "data"],
    "domains": ["software", "data"],
    "topic": "sql",
    "difficulty": "hard",
    "skill_tag": "correlated_subquery",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "sql_window_fn_01",
    "prompt": "Which SQL function would you use to rank rows within each department by salary without collapsing them into groups?",
    "options": [
        {"id": "a", "text": "RANK() OVER (PARTITION BY dept ORDER BY salary DESC)"},
        {"id": "b", "text": "GROUP BY dept ORDER BY salary DESC"},
        {"id": "c", "text": "MAX(salary) GROUP BY dept"},
        {"id": "d", "text": "SELECT DISTINCT salary FROM table"}
    ],
    "correct_option_id": "a",
    "explanation": "Window functions like RANK() operate over a window of rows without reducing them, unlike GROUP BY.",
    "role_families": ["data", "backend"],
    "domains": ["data", "software"],
    "topic": "sql",
    "difficulty": "hard",
    "skill_tag": "window_functions",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},

# ===========================================================================
# API — additional
# ===========================================================================
{
    "question_id": "api_auth_jwt_01",
    "prompt": "What is a JWT (JSON Web Token) primarily used for?",
    "options": [
        {"id": "a", "text": "Securely transmitting claims between parties as a signed, self-contained token"},
        {"id": "b", "text": "Encrypting database rows"},
        {"id": "c", "text": "Compressing HTTP responses"},
        {"id": "d", "text": "Replacing SQL queries"}
    ],
    "correct_option_id": "a",
    "explanation": "JWTs carry signed claims (user identity, roles) and can be verified without a database lookup.",
    "role_families": ["backend", "software_foundations", "security"],
    "domains": ["software"],
    "topic": "api",
    "difficulty": "medium",
    "skill_tag": "jwt_auth",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "api_rate_limit_01",
    "prompt": "Why do production APIs implement rate limiting?",
    "options": [
        {"id": "a", "text": "To protect the service from being overwhelmed and to ensure fair usage"},
        {"id": "b", "text": "To encrypt all requests automatically"},
        {"id": "c", "text": "To avoid using a database"},
        {"id": "d", "text": "To reduce the number of HTTP methods available"}
    ],
    "correct_option_id": "a",
    "explanation": "Rate limiting caps requests per client over time, preventing abuse and keeping the service stable.",
    "role_families": ["backend", "software_foundations"],
    "domains": ["software"],
    "topic": "api",
    "difficulty": "medium",
    "skill_tag": "rate_limiting",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "api_rest_vs_graphql_01",
    "prompt": "What is one advantage of GraphQL over REST for data fetching?",
    "options": [
        {"id": "a", "text": "Clients can request exactly the fields they need, avoiding over- or under-fetching"},
        {"id": "b", "text": "GraphQL requires no schema"},
        {"id": "c", "text": "GraphQL always responds faster than REST"},
        {"id": "d", "text": "GraphQL does not use HTTP"}
    ],
    "correct_option_id": "a",
    "explanation": "GraphQL lets clients specify which fields to return in a single query, reducing unnecessary data transfer.",
    "role_families": ["backend", "frontend"],
    "domains": ["software"],
    "topic": "api",
    "difficulty": "medium",
    "skill_tag": "graphql_vs_rest",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},

# ===========================================================================
# BACKEND — additional
# ===========================================================================
{
    "question_id": "backend_caching_01",
    "prompt": "What problem does an application-level cache (e.g. Redis) primarily solve?",
    "options": [
        {"id": "a", "text": "Reducing repeated expensive database queries by storing results in fast memory"},
        {"id": "b", "text": "Replacing the relational database entirely"},
        {"id": "c", "text": "Encrypting HTTP payloads"},
        {"id": "d", "text": "Managing CPU threads"}
    ],
    "correct_option_id": "a",
    "explanation": "Caches store frequently read data in RAM so repeated requests skip the database, reducing latency.",
    "role_families": ["backend", "devops"],
    "domains": ["software"],
    "topic": "backend",
    "difficulty": "easy",
    "skill_tag": "caching_basics",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "backend_message_queue_01",
    "prompt": "Why is a message queue useful in a microservices architecture?",
    "options": [
        {"id": "a", "text": "It decouples services so they do not need to be available at the same time"},
        {"id": "b", "text": "It replaces load balancers"},
        {"id": "c", "text": "It stores SQL schemas"},
        {"id": "d", "text": "It compiles code across services"}
    ],
    "correct_option_id": "a",
    "explanation": "A message queue (e.g. RabbitMQ, Kafka) buffers messages so producer and consumer services are temporally decoupled.",
    "role_families": ["backend", "devops", "data"],
    "domains": ["software", "data"],
    "topic": "backend",
    "difficulty": "medium",
    "skill_tag": "message_queue",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "backend_n_plus_one_01",
    "prompt": "What is the N+1 query problem in ORM-based applications?",
    "options": [
        {"id": "a", "text": "One query fetches N rows, then N additional queries are run for each row's related data"},
        {"id": "b", "text": "A query returns N+1 columns instead of N"},
        {"id": "c", "text": "An ORM executes one query per database table"},
        {"id": "d", "text": "A transaction requires N+1 commits to succeed"}
    ],
    "correct_option_id": "a",
    "explanation": "N+1 happens when code iterates N results and issues an additional DB call per item instead of joining or eager-loading.",
    "role_families": ["backend"],
    "domains": ["software"],
    "topic": "backend",
    "difficulty": "hard",
    "skill_tag": "n_plus_one",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},

# ===========================================================================
# DEBUGGING — additional
# ===========================================================================
{
    "question_id": "debugging_rubber_duck_01",
    "prompt": "What technique involves explaining your code step-by-step to an inanimate object to uncover bugs?",
    "options": [
        {"id": "a", "text": "Rubber duck debugging"},
        {"id": "b", "text": "Pair programming"},
        {"id": "c", "text": "Regression testing"},
        {"id": "d", "text": "Code review"}
    ],
    "correct_option_id": "a",
    "explanation": "Articulating the code's logic aloud often reveals the mistake because it forces precise thinking.",
    "role_families": ["software_foundations", "backend", "frontend"],
    "domains": ["software"],
    "topic": "debugging",
    "difficulty": "easy",
    "skill_tag": "rubber_duck_debugging",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "debugging_binary_search_code_01",
    "prompt": "You have a function that fails on some inputs. What is an efficient strategy to find the exact line causing the failure?",
    "options": [
        {"id": "a", "text": "Add breakpoints or print statements at the midpoint, then narrow down which half contains the bug"},
        {"id": "b", "text": "Rewrite the entire function from scratch"},
        {"id": "c", "text": "Remove all error handling"},
        {"id": "d", "text": "Run the program on a different machine"}
    ],
    "correct_option_id": "a",
    "explanation": "Binary search debugging isolates the fault by repeatedly halving the suspected code region.",
    "role_families": ["software_foundations", "backend", "frontend", "qa"],
    "domains": ["software"],
    "topic": "debugging",
    "difficulty": "medium",
    "skill_tag": "debugging_bisect",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "debugging_stack_trace_01",
    "prompt": "When reading an error stack trace, which entry is the most useful starting point?",
    "options": [
        {"id": "a", "text": "The innermost (bottom-most) frame in your own code where the error actually originated"},
        {"id": "b", "text": "The first line of the trace regardless of source"},
        {"id": "c", "text": "Framework or library frames near the top"},
        {"id": "d", "text": "The last line of the trace always"}
    ],
    "correct_option_id": "a",
    "explanation": "The deepest frame in your own application code is usually where the root cause lives.",
    "role_families": ["software_foundations", "backend", "frontend"],
    "domains": ["software"],
    "topic": "debugging",
    "difficulty": "medium",
    "skill_tag": "stack_trace_reading",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},

# ===========================================================================
# OPERATING SYSTEMS — additional
# ===========================================================================
{
    "question_id": "os_scheduler_01",
    "prompt": "Round-Robin CPU scheduling assigns each process:",
    "options": [
        {"id": "a", "text": "A fixed time slice and preempts it when the slice expires"},
        {"id": "b", "text": "Unlimited CPU time until it voluntarily releases"},
        {"id": "c", "text": "Priority based on memory usage"},
        {"id": "d", "text": "A permanent CPU core"}
    ],
    "correct_option_id": "a",
    "explanation": "Round-Robin is a preemptive algorithm that gives each process a time quantum and cycles through them fairly.",
    "role_families": ["software_foundations", "backend", "embedded", "robotics"],
    "domains": ["software"],
    "topic": "operating_systems",
    "difficulty": "easy",
    "skill_tag": "round_robin_scheduling",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "os_semaphore_01",
    "prompt": "What is the main purpose of a semaphore in concurrent programming?",
    "options": [
        {"id": "a", "text": "To control access to a shared resource by allowing only a set number of threads at a time"},
        {"id": "b", "text": "To permanently block all other threads"},
        {"id": "c", "text": "To copy data between processes"},
        {"id": "d", "text": "To sort threads by creation time"}
    ],
    "correct_option_id": "a",
    "explanation": "A semaphore is a signalling mechanism that limits concurrent access to a resource to n threads.",
    "role_families": ["software_foundations", "backend", "embedded"],
    "domains": ["software"],
    "topic": "operating_systems",
    "difficulty": "medium",
    "skill_tag": "semaphore",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "os_paging_01",
    "prompt": "What problem does memory paging solve in an operating system?",
    "options": [
        {"id": "a", "text": "It allows memory to be allocated in fixed-size chunks, avoiding external fragmentation"},
        {"id": "b", "text": "It makes disk I/O faster by caching reads"},
        {"id": "c", "text": "It prevents all page faults"},
        {"id": "d", "text": "It replaces the CPU cache"}
    ],
    "correct_option_id": "a",
    "explanation": "Paging divides physical and virtual memory into equal-size pages/frames, eliminating external fragmentation.",
    "role_families": ["software_foundations", "embedded"],
    "domains": ["software"],
    "topic": "operating_systems",
    "difficulty": "medium",
    "skill_tag": "memory_paging",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},

# ===========================================================================
# DBMS — additional
# ===========================================================================
{
    "question_id": "dbms_normalization_01",
    "prompt": "What does 3NF (Third Normal Form) primarily eliminate from a relational database?",
    "options": [
        {"id": "a", "text": "Transitive dependencies between non-key columns"},
        {"id": "b", "text": "All duplicate rows"},
        {"id": "c", "text": "NULL values"},
        {"id": "d", "text": "The need for foreign keys"}
    ],
    "correct_option_id": "a",
    "explanation": "3NF requires that non-key attributes depend only on the primary key, not on other non-key attributes.",
    "role_families": ["backend", "data"],
    "domains": ["software", "data"],
    "topic": "dbms",
    "difficulty": "hard",
    "skill_tag": "normalization_3nf",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "dbms_foreign_key_01",
    "prompt": "What does a FOREIGN KEY constraint enforce in a relational database?",
    "options": [
        {"id": "a", "text": "Referential integrity — the referenced row must exist in the parent table"},
        {"id": "b", "text": "That the column is always unique"},
        {"id": "c", "text": "That the column cannot contain NULL"},
        {"id": "d", "text": "That the column is the primary key"}
    ],
    "correct_option_id": "a",
    "explanation": "A foreign key ensures that a value in one table references an existing row in the related table.",
    "role_families": ["backend", "data"],
    "domains": ["software", "data"],
    "topic": "dbms",
    "difficulty": "easy",
    "skill_tag": "foreign_key",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "dbms_index_type_01",
    "prompt": "What is a composite index and when is it most useful?",
    "options": [
        {"id": "a", "text": "An index on multiple columns; most useful for queries that filter on those columns together"},
        {"id": "b", "text": "An index that stores the full row data"},
        {"id": "c", "text": "An index used only for text search"},
        {"id": "d", "text": "An index that covers only the primary key"}
    ],
    "correct_option_id": "a",
    "explanation": "A composite (multi-column) index speeds up queries that filter or sort by the indexed columns in order.",
    "role_families": ["backend", "data"],
    "domains": ["software", "data"],
    "topic": "dbms",
    "difficulty": "medium",
    "skill_tag": "composite_index",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},

# ===========================================================================
# ML / AI / DATA — additional
# ===========================================================================
{
    "question_id": "ml_overfitting_01",
    "prompt": "A machine learning model has very high training accuracy but poor validation accuracy. This is most likely caused by:",
    "options": [
        {"id": "a", "text": "Overfitting — the model memorised training data and generalises poorly"},
        {"id": "b", "text": "Underfitting — the model is too simple"},
        {"id": "c", "text": "A corrupt test set"},
        {"id": "d", "text": "Using too few epochs"}
    ],
    "correct_option_id": "a",
    "explanation": "High train accuracy and low validation accuracy is the classic signature of overfitting.",
    "role_families": ["ai_ml", "data"],
    "domains": ["ai_ml", "data"],
    "topic": "ml_basics",
    "difficulty": "easy",
    "skill_tag": "overfitting",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "ml_regularisation_01",
    "prompt": "L2 regularisation (Ridge) in a linear model penalises:",
    "options": [
        {"id": "a", "text": "The sum of squared weights, shrinking them towards zero"},
        {"id": "b", "text": "The number of training samples"},
        {"id": "c", "text": "The learning rate automatically"},
        {"id": "d", "text": "Only the bias term"}
    ],
    "correct_option_id": "a",
    "explanation": "L2 adds λ*||w||^2 to the loss, penalising large weights and reducing overfitting.",
    "role_families": ["ai_ml", "data"],
    "domains": ["ai_ml", "data"],
    "topic": "ml_basics",
    "difficulty": "medium",
    "skill_tag": "l2_regularisation",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "ml_confusion_matrix_01",
    "prompt": "In a binary classifier, a False Positive (FP) is when the model predicts:",
    "options": [
        {"id": "a", "text": "Positive but the true label is Negative"},
        {"id": "b", "text": "Negative but the true label is Positive"},
        {"id": "c", "text": "Positive and the true label is also Positive"},
        {"id": "d", "text": "Negative and the true label is also Negative"}
    ],
    "correct_option_id": "a",
    "explanation": "A False Positive is an incorrect positive prediction — model says yes but reality is no.",
    "role_families": ["ai_ml", "data"],
    "domains": ["ai_ml", "data"],
    "topic": "ml_basics",
    "difficulty": "medium",
    "skill_tag": "confusion_matrix",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "ml_gradient_descent_01",
    "prompt": "What does the learning rate control in gradient descent?",
    "options": [
        {"id": "a", "text": "The size of the parameter update step at each iteration"},
        {"id": "b", "text": "The number of hidden layers"},
        {"id": "c", "text": "The batch size"},
        {"id": "d", "text": "The activation function"}
    ],
    "correct_option_id": "a",
    "explanation": "A high learning rate takes large steps (risk of divergence); a low rate takes small steps (slow convergence).",
    "role_families": ["ai_ml", "data"],
    "domains": ["ai_ml"],
    "topic": "ml_basics",
    "difficulty": "easy",
    "skill_tag": "learning_rate",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "ml_transformer_attention_01",
    "prompt": "The self-attention mechanism in Transformer models mainly allows each token to:",
    "options": [
        {"id": "a", "text": "Attend to all other tokens in the sequence to capture contextual relationships"},
        {"id": "b", "text": "Only look at the token immediately to its left"},
        {"id": "c", "text": "Skip unknown tokens"},
        {"id": "d", "text": "Compress the sequence to a single vector before processing"}
    ],
    "correct_option_id": "a",
    "explanation": "Self-attention computes relevance scores between every pair of tokens, giving full context regardless of distance.",
    "role_families": ["ai_ml"],
    "domains": ["ai_ml"],
    "topic": "ml_basics",
    "difficulty": "hard",
    "skill_tag": "transformer_attention",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},

# LLM / AI engineer questions
{
    "question_id": "llm_hallucination_01",
    "prompt": "What is LLM 'hallucination' in the context of generative AI?",
    "options": [
        {"id": "a", "text": "The model generating confident but factually incorrect or fabricated information"},
        {"id": "b", "text": "The model refusing to answer any question"},
        {"id": "c", "text": "The model outputting only images"},
        {"id": "d", "text": "The model crashing during inference"}
    ],
    "correct_option_id": "a",
    "explanation": "Hallucination refers to an LLM producing plausible-sounding but false or invented content.",
    "role_families": ["ai_ml"],
    "domains": ["ai_ml"],
    "topic": "llm_basics",
    "difficulty": "easy",
    "skill_tag": "llm_hallucination",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "llm_rag_01",
    "prompt": "What is Retrieval-Augmented Generation (RAG) used for?",
    "options": [
        {"id": "a", "text": "Grounding LLM responses in retrieved documents to reduce hallucination and add up-to-date knowledge"},
        {"id": "b", "text": "Training the LLM on private data from scratch"},
        {"id": "c", "text": "Compressing the model to run on mobile"},
        {"id": "d", "text": "Replacing the tokeniser"}
    ],
    "correct_option_id": "a",
    "explanation": "RAG retrieves relevant passages at inference time and injects them into the prompt so the LLM can answer from facts.",
    "role_families": ["ai_ml"],
    "domains": ["ai_ml"],
    "topic": "llm_basics",
    "difficulty": "medium",
    "skill_tag": "rag",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "llm_token_01",
    "prompt": "In the context of LLMs, what is a 'token'?",
    "options": [
        {"id": "a", "text": "A unit of text (a word, subword, or character) that the model processes as a single input element"},
        {"id": "b", "text": "An authentication credential"},
        {"id": "c", "text": "A GPU memory block"},
        {"id": "d", "text": "A checkpoint file"}
    ],
    "correct_option_id": "a",
    "explanation": "LLMs convert text into tokens (often subword pieces) before processing. Costs and context limits are measured in tokens.",
    "role_families": ["ai_ml"],
    "domains": ["ai_ml"],
    "topic": "llm_basics",
    "difficulty": "easy",
    "skill_tag": "llm_tokens",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},

# Prompt engineering
{
    "question_id": "prompt_few_shot_01",
    "prompt": "What is few-shot prompting?",
    "options": [
        {"id": "a", "text": "Including a small number of input-output examples in the prompt to guide the model's response format"},
        {"id": "b", "text": "Fine-tuning the model on a few thousand samples"},
        {"id": "c", "text": "Using a small model with few parameters"},
        {"id": "d", "text": "Sending multiple API calls simultaneously"}
    ],
    "correct_option_id": "a",
    "explanation": "Few-shot prompting demonstrates the task with 2-10 examples in the prompt so the model infers the pattern.",
    "role_families": ["ai_ml"],
    "domains": ["ai_ml"],
    "topic": "prompt_engineering",
    "difficulty": "easy",
    "skill_tag": "few_shot_prompting",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "prompt_system_role_01",
    "prompt": "In chat-based LLM APIs, the system message is primarily used to:",
    "options": [
        {"id": "a", "text": "Set the model's persona, behaviour, and constraints for the entire conversation"},
        {"id": "b", "text": "Store authentication tokens"},
        {"id": "c", "text": "Define the database schema"},
        {"id": "d", "text": "Limit the number of tokens returned"}
    ],
    "correct_option_id": "a",
    "explanation": "The system prompt shapes how the assistant behaves throughout the session, e.g. tone, rules, and domain focus.",
    "role_families": ["ai_ml"],
    "domains": ["ai_ml"],
    "topic": "prompt_engineering",
    "difficulty": "easy",
    "skill_tag": "system_message",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},

# Vector search
{
    "question_id": "vector_embedding_01",
    "prompt": "What is a vector embedding?",
    "options": [
        {"id": "a", "text": "A dense numerical representation that captures the semantic meaning of text or data"},
        {"id": "b", "text": "A Base64-encoded binary file"},
        {"id": "c", "text": "A type of SQL index"},
        {"id": "d", "text": "A compressed audio format"}
    ],
    "correct_option_id": "a",
    "explanation": "Embeddings place semantically similar items close together in high-dimensional vector space.",
    "role_families": ["ai_ml"],
    "domains": ["ai_ml"],
    "topic": "vector_search",
    "difficulty": "easy",
    "skill_tag": "vector_embedding",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "vector_cosine_similarity_01",
    "prompt": "Cosine similarity measures the angle between two vectors. A cosine similarity of 1 means:",
    "options": [
        {"id": "a", "text": "The vectors point in the same direction (identical semantics)"},
        {"id": "b", "text": "The vectors are perpendicular (orthogonal)"},
        {"id": "c", "text": "One vector is the inverse of the other"},
        {"id": "d", "text": "The magnitude of both vectors is 1"}
    ],
    "correct_option_id": "a",
    "explanation": "Cosine similarity = 1 when vectors are parallel, 0 when orthogonal, -1 when opposite.",
    "role_families": ["ai_ml", "data"],
    "domains": ["ai_ml"],
    "topic": "vector_search",
    "difficulty": "medium",
    "skill_tag": "cosine_similarity",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},

# ===========================================================================
# DATA — pandas, statistics, pipelines (additional)
# ===========================================================================
{
    "question_id": "pandas_fillna_01",
    "prompt": "In pandas, what does df['col'].fillna(0) do?",
    "options": [
        {"id": "a", "text": "Replaces NaN values in 'col' with 0"},
        {"id": "b", "text": "Deletes rows where 'col' is NaN"},
        {"id": "c", "text": "Sets all values in 'col' to 0"},
        {"id": "d", "text": "Checks if 'col' has any NaN values"}
    ],
    "correct_option_id": "a",
    "explanation": "fillna replaces missing values with the specified replacement value.",
    "role_families": ["data", "ai_ml"],
    "domains": ["data", "ai_ml"],
    "topic": "pandas",
    "difficulty": "easy",
    "skill_tag": "pandas_fillna",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "pandas_groupby_agg_01",
    "prompt": "What does df.groupby('dept')['salary'].mean() compute?",
    "options": [
        {"id": "a", "text": "The average salary for each unique department"},
        {"id": "b", "text": "The maximum salary overall"},
        {"id": "c", "text": "All salaries sorted by department"},
        {"id": "d", "text": "The number of employees per department"}
    ],
    "correct_option_id": "a",
    "explanation": "groupby groups rows by dept, then mean() computes the average of the salary column per group.",
    "role_families": ["data", "ai_ml"],
    "domains": ["data"],
    "topic": "pandas",
    "difficulty": "easy",
    "skill_tag": "pandas_groupby",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "stats_central_limit_01",
    "prompt": "The Central Limit Theorem states that the distribution of sample means approaches:",
    "options": [
        {"id": "a", "text": "A normal distribution as sample size increases, regardless of the population distribution"},
        {"id": "b", "text": "A uniform distribution for large samples"},
        {"id": "c", "text": "The original population distribution exactly"},
        {"id": "d", "text": "A Poisson distribution for count data"}
    ],
    "correct_option_id": "a",
    "explanation": "CLT guarantees that with large enough n, the sampling distribution of the mean is approximately Gaussian.",
    "role_families": ["data", "ai_ml"],
    "domains": ["data", "ai_ml"],
    "topic": "statistics",
    "difficulty": "medium",
    "skill_tag": "central_limit_theorem",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "stats_p_value_01",
    "prompt": "A p-value of 0.03 under a significance level of 0.05 means you should:",
    "options": [
        {"id": "a", "text": "Reject the null hypothesis — the result is statistically significant"},
        {"id": "b", "text": "Accept the null hypothesis"},
        {"id": "c", "text": "Re-run the test with more data before deciding"},
        {"id": "d", "text": "Lower the significance level to 0.01"}
    ],
    "correct_option_id": "a",
    "explanation": "p < α means the observed result is unlikely under H₀, so you reject it.",
    "role_families": ["data", "ai_ml"],
    "domains": ["data"],
    "topic": "statistics",
    "difficulty": "medium",
    "skill_tag": "p_value",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "data_pipeline_idempotency_01",
    "prompt": "An idempotent data pipeline operation means running it multiple times produces:",
    "options": [
        {"id": "a", "text": "The same result as running it once — no duplicate or corrupted state"},
        {"id": "b", "text": "More rows each run"},
        {"id": "c", "text": "Different output each time for variety"},
        {"id": "d", "text": "An error on the second run"}
    ],
    "correct_option_id": "a",
    "explanation": "Idempotency is crucial for pipelines with retries — re-running should not corrupt or duplicate data.",
    "role_families": ["data", "devops"],
    "domains": ["data"],
    "topic": "data_pipelines",
    "difficulty": "medium",
    "skill_tag": "pipeline_idempotency",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},

# ===========================================================================
# DEVOPS / CLOUD / SECURITY — additional
# ===========================================================================
{
    "question_id": "devops_ci_cd_stages_01",
    "prompt": "In a typical CI/CD pipeline, what happens during the 'Continuous Integration' phase?",
    "options": [
        {"id": "a", "text": "Code is built and tests run automatically on every push to detect integration issues early"},
        {"id": "b", "text": "Code is deployed to production without human approval"},
        {"id": "c", "text": "Infrastructure is provisioned from scratch"},
        {"id": "d", "text": "Database migrations are rolled back"}
    ],
    "correct_option_id": "a",
    "explanation": "CI automates building and testing so integration problems are caught immediately on each commit.",
    "role_families": ["devops", "backend", "software_foundations"],
    "domains": ["devops", "software"],
    "topic": "ci_cd",
    "difficulty": "easy",
    "skill_tag": "ci_basics",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "devops_blue_green_01",
    "prompt": "What is the main advantage of a Blue-Green deployment strategy?",
    "options": [
        {"id": "a", "text": "Zero-downtime rollout — traffic is switched to the new version only after it is verified healthy"},
        {"id": "b", "text": "Half the infrastructure cost"},
        {"id": "c", "text": "Automatic database rollback"},
        {"id": "d", "text": "Slower but safer builds"}
    ],
    "correct_option_id": "a",
    "explanation": "Blue-Green keeps the old environment live while the new one warms up, enabling instant cut-over and rollback.",
    "role_families": ["devops"],
    "domains": ["devops"],
    "topic": "ci_cd",
    "difficulty": "medium",
    "skill_tag": "blue_green_deploy",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "cloud_serverless_01",
    "prompt": "What is a key advantage of serverless functions (e.g. AWS Lambda) compared to always-on servers?",
    "options": [
        {"id": "a", "text": "You pay only when code executes and capacity scales automatically with demand"},
        {"id": "b", "text": "They have unlimited execution duration"},
        {"id": "c", "text": "They always run faster than containerised services"},
        {"id": "d", "text": "They eliminate the need for any networking configuration"}
    ],
    "correct_option_id": "a",
    "explanation": "Serverless charges per execution and auto-scales, removing the need to provision or manage servers.",
    "role_families": ["devops", "backend", "data"],
    "domains": ["devops", "software"],
    "topic": "cloud",
    "difficulty": "easy",
    "skill_tag": "serverless_basics",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "cloud_iac_01",
    "prompt": "Infrastructure as Code (IaC) means:",
    "options": [
        {"id": "a", "text": "Managing and provisioning infrastructure through machine-readable configuration files instead of manual steps"},
        {"id": "b", "text": "Writing application code that runs on cloud VMs"},
        {"id": "c", "text": "Storing source code in a cloud object store"},
        {"id": "d", "text": "Using only managed databases in the cloud"}
    ],
    "correct_option_id": "a",
    "explanation": "IaC (Terraform, CloudFormation, etc.) makes infrastructure reproducible, version-controlled, and automatable.",
    "role_families": ["devops"],
    "domains": ["devops"],
    "topic": "cloud",
    "difficulty": "easy",
    "skill_tag": "infrastructure_as_code",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "security_xss_01",
    "prompt": "Cross-Site Scripting (XSS) attacks work by:",
    "options": [
        {"id": "a", "text": "Injecting malicious scripts into web pages that are then executed in other users' browsers"},
        {"id": "b", "text": "Stealing database credentials from the server"},
        {"id": "c", "text": "Flooding a server with requests"},
        {"id": "d", "text": "Intercepting HTTPS traffic"}
    ],
    "correct_option_id": "a",
    "explanation": "XSS exploits insufficient output encoding to inject scripts that run in victims' browsers and steal data or sessions.",
    "role_families": ["security", "backend", "frontend", "software_foundations"],
    "domains": ["security", "software"],
    "topic": "web_security",
    "difficulty": "medium",
    "skill_tag": "xss_attack",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "security_sql_injection_01",
    "prompt": "What is the most effective defence against SQL injection attacks?",
    "options": [
        {"id": "a", "text": "Using parameterised queries (prepared statements) so user input is never concatenated into SQL"},
        {"id": "b", "text": "Disabling the database"},
        {"id": "c", "text": "Encoding all output as HTML"},
        {"id": "d", "text": "Using HTTP instead of HTTPS"}
    ],
    "correct_option_id": "a",
    "explanation": "Parameterised queries separate SQL code from data, making injection impossible regardless of input content.",
    "role_families": ["backend", "security", "software_foundations"],
    "domains": ["software", "security"],
    "topic": "web_security",
    "difficulty": "medium",
    "skill_tag": "sql_injection_defence",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "security_owasp_01",
    "prompt": "Which of the following is consistently listed in the OWASP Top 10 security risks?",
    "options": [
        {"id": "a", "text": "Broken access control (authorisation failures)"},
        {"id": "b", "text": "Using too many microservices"},
        {"id": "c", "text": "Writing code in Python"},
        {"id": "d", "text": "Using open-source libraries"}
    ],
    "correct_option_id": "a",
    "explanation": "Broken access control has topped the OWASP Top 10 in recent years — improper permission checks are extremely common.",
    "role_families": ["security", "backend"],
    "domains": ["security"],
    "topic": "security_basics",
    "difficulty": "easy",
    "skill_tag": "owasp_access_control",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},

# ===========================================================================
# FRONTEND — additional
# ===========================================================================
{
    "question_id": "frontend_dom_event_01",
    "prompt": "What is event bubbling in the browser DOM?",
    "options": [
        {"id": "a", "text": "Events fired on a child element propagate up through ancestor elements"},
        {"id": "b", "text": "Events are queued and processed in batch"},
        {"id": "c", "text": "An event fires multiple times on the same element"},
        {"id": "d", "text": "A click event triggers a keyboard event"}
    ],
    "correct_option_id": "a",
    "explanation": "When an event fires, it bubbles from the target element up through each parent in the DOM tree.",
    "role_families": ["frontend"],
    "domains": ["software"],
    "topic": "browser",
    "difficulty": "medium",
    "skill_tag": "event_bubbling",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "frontend_cors_01",
    "prompt": "What does CORS (Cross-Origin Resource Sharing) control?",
    "options": [
        {"id": "a", "text": "Which origins (domains) are allowed to make requests to a given server"},
        {"id": "b", "text": "The compression format of HTTP responses"},
        {"id": "c", "text": "Cookie encryption"},
        {"id": "d", "text": "DNS resolution order"}
    ],
    "correct_option_id": "a",
    "explanation": "CORS headers on the server specify which foreign origins browsers are allowed to make cross-domain requests from.",
    "role_families": ["frontend", "backend"],
    "domains": ["software"],
    "topic": "browser",
    "difficulty": "medium",
    "skill_tag": "cors",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "frontend_vdom_01",
    "prompt": "React's Virtual DOM improves performance by:",
    "options": [
        {"id": "a", "text": "Computing the minimal set of real DOM changes needed and batching them"},
        {"id": "b", "text": "Replacing the browser's DOM engine entirely"},
        {"id": "c", "text": "Caching API responses in localStorage"},
        {"id": "d", "text": "Compiling JSX to WebAssembly"}
    ],
    "correct_option_id": "a",
    "explanation": "React diffs the virtual DOM tree, calculates minimal patches, then applies only the needed real DOM mutations.",
    "role_families": ["frontend"],
    "domains": ["software"],
    "topic": "react",
    "difficulty": "medium",
    "skill_tag": "virtual_dom",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "frontend_use_effect_01",
    "prompt": "In React, the useEffect hook with an empty dependency array [] runs:",
    "options": [
        {"id": "a", "text": "Once after the initial render only"},
        {"id": "b", "text": "On every render"},
        {"id": "c", "text": "Never"},
        {"id": "d", "text": "Before the initial render"}
    ],
    "correct_option_id": "a",
    "explanation": "An empty dependency array tells React the effect has no dependencies, so it runs only once after mount.",
    "role_families": ["frontend"],
    "domains": ["software"],
    "topic": "react",
    "difficulty": "easy",
    "skill_tag": "use_effect_empty_deps",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "frontend_css_flexbox_01",
    "prompt": "Which CSS property makes children of a container line up horizontally with equal space between them?",
    "options": [
        {"id": "a", "text": "display: flex; justify-content: space-between"},
        {"id": "b", "text": "display: block; margin: auto"},
        {"id": "c", "text": "position: absolute; top: 0"},
        {"id": "d", "text": "float: left on each child"}
    ],
    "correct_option_id": "a",
    "explanation": "flexbox with justify-content: space-between distributes children horizontally with maximum gaps between them.",
    "role_families": ["frontend"],
    "domains": ["software"],
    "topic": "css_layout",
    "difficulty": "easy",
    "skill_tag": "flexbox_space_between",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},

# ===========================================================================
# PYTHON — additional (hard concepts)
# ===========================================================================
{
    "question_id": "python_decorator_01",
    "prompt": "What does a Python decorator do?",
    "options": [
        {"id": "a", "text": "Wraps a function to add behaviour before or after it runs without changing its source code"},
        {"id": "b", "text": "Converts a function to a class"},
        {"id": "c", "text": "Inlines the function at every call site for speed"},
        {"id": "d", "text": "Marks the function as a coroutine"}
    ],
    "correct_option_id": "a",
    "explanation": "@decorator syntax applies a wrapper function, enabling cross-cutting concerns like logging or timing.",
    "role_families": ["backend", "data", "ai_ml"],
    "domains": ["software"],
    "topic": "python",
    "difficulty": "medium",
    "skill_tag": "python_decorator",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "python_gil_01",
    "prompt": "What does the Python GIL (Global Interpreter Lock) prevent?",
    "options": [
        {"id": "a", "text": "Multiple Python threads from executing Python bytecode simultaneously"},
        {"id": "b", "text": "Using more than one CPU core for any Python program"},
        {"id": "c", "text": "Importing third-party packages"},
        {"id": "d", "text": "Garbage collection from running during I/O"}
    ],
    "correct_option_id": "a",
    "explanation": "The GIL ensures only one thread executes Python bytecode at a time, limiting CPU parallelism in CPython.",
    "role_families": ["backend", "data", "ai_ml"],
    "domains": ["software"],
    "topic": "python",
    "difficulty": "hard",
    "skill_tag": "python_gil",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "python_context_manager_01",
    "prompt": "What is the primary purpose of Python's 'with' statement?",
    "options": [
        {"id": "a", "text": "To ensure setup and teardown (e.g. file close) happen even if an exception occurs"},
        {"id": "b", "text": "To import a module temporarily"},
        {"id": "c", "text": "To define an anonymous function"},
        {"id": "d", "text": "To switch between virtual environments"}
    ],
    "correct_option_id": "a",
    "explanation": "'with' invokes a context manager's __enter__ and __exit__, guaranteeing cleanup even on exceptions.",
    "role_families": ["backend", "data", "ai_ml"],
    "domains": ["software"],
    "topic": "python",
    "difficulty": "easy",
    "skill_tag": "context_manager",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},

# ===========================================================================
# QA / TESTING — additional
# ===========================================================================
{
    "question_id": "qa_unit_test_01",
    "prompt": "What is a unit test?",
    "options": [
        {"id": "a", "text": "A test that verifies a single, isolated function or component in isolation from other parts"},
        {"id": "b", "text": "A test that runs the entire application end to end"},
        {"id": "c", "text": "A test that measures system performance under load"},
        {"id": "d", "text": "A manual test performed by QA engineers"}
    ],
    "correct_option_id": "a",
    "explanation": "Unit tests target the smallest testable piece of code in isolation, using mocks for dependencies.",
    "role_families": ["qa", "software_foundations", "backend"],
    "domains": ["software"],
    "topic": "testing",
    "difficulty": "easy",
    "skill_tag": "unit_testing",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "qa_test_pyramid_01",
    "prompt": "The testing pyramid suggests that you should have the most tests at which level?",
    "options": [
        {"id": "a", "text": "Unit tests — they are fast and cheap"},
        {"id": "b", "text": "End-to-end tests — they cover the most scenarios"},
        {"id": "c", "text": "Manual tests — they are the most reliable"},
        {"id": "d", "text": "Integration tests — they test real services"}
    ],
    "correct_option_id": "a",
    "explanation": "The pyramid has many cheap unit tests at the base, fewer integration tests in the middle, and few slow E2E tests at the top.",
    "role_families": ["qa", "software_foundations", "backend"],
    "domains": ["software"],
    "topic": "testing",
    "difficulty": "easy",
    "skill_tag": "test_pyramid",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "qa_mocking_01",
    "prompt": "Why do unit tests use mocks or stubs?",
    "options": [
        {"id": "a", "text": "To replace real dependencies (databases, APIs) with controlled fakes so tests are fast and isolated"},
        {"id": "b", "text": "To make test results non-deterministic for realism"},
        {"id": "c", "text": "To run tests on a production database safely"},
        {"id": "d", "text": "To avoid writing assertions"}
    ],
    "correct_option_id": "a",
    "explanation": "Mocks isolate the unit under test from external systems, keeping tests deterministic and fast.",
    "role_families": ["qa", "backend", "software_foundations"],
    "domains": ["software"],
    "topic": "testing",
    "difficulty": "easy",
    "skill_tag": "mocking",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "qa_regression_01",
    "prompt": "What is a regression test?",
    "options": [
        {"id": "a", "text": "A test that re-verifies previously working functionality after new changes to ensure nothing broke"},
        {"id": "b", "text": "A test that checks performance metrics"},
        {"id": "c", "text": "A test run only before the first release"},
        {"id": "d", "text": "A statistical model fitted to test data"}
    ],
    "correct_option_id": "a",
    "explanation": "Regression tests guard against reintroducing bugs that were previously fixed.",
    "role_families": ["qa", "software_foundations"],
    "domains": ["software"],
    "topic": "testing",
    "difficulty": "easy",
    "skill_tag": "regression_testing",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},

# ===========================================================================
# EMBEDDED / IOT / VLSI / ROBOTICS — additional
# ===========================================================================
{
    "question_id": "embedded_interrupt_01",
    "prompt": "What is an interrupt in embedded systems?",
    "options": [
        {"id": "a", "text": "A hardware or software signal that pauses the CPU's current task to handle a higher-priority event"},
        {"id": "b", "text": "A software timer that fires every millisecond"},
        {"id": "c", "text": "A memory protection violation"},
        {"id": "d", "text": "A watchdog reset"}
    ],
    "correct_option_id": "a",
    "explanation": "Interrupts allow real-time systems to respond to events (GPIO, UART) immediately without constant polling.",
    "role_families": ["embedded", "robotics"],
    "domains": ["embedded"],
    "topic": "microcontrollers",
    "difficulty": "easy",
    "skill_tag": "interrupt_basics",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "embedded_watchdog_01",
    "prompt": "What is the purpose of a watchdog timer in embedded systems?",
    "options": [
        {"id": "a", "text": "To reset the microcontroller if the firmware hangs and fails to 'kick' the watchdog in time"},
        {"id": "b", "text": "To measure the real-time clock"},
        {"id": "c", "text": "To manage memory allocation"},
        {"id": "d", "text": "To control PWM output frequency"}
    ],
    "correct_option_id": "a",
    "explanation": "A watchdog timer recovers from firmware lockups by issuing a reset if not periodically refreshed by the main loop.",
    "role_families": ["embedded"],
    "domains": ["embedded"],
    "topic": "microcontrollers",
    "difficulty": "medium",
    "skill_tag": "watchdog_timer",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "embedded_i2c_vs_spi_01",
    "prompt": "Which communication protocol requires fewer wires but is generally slower: I2C or SPI?",
    "options": [
        {"id": "a", "text": "I2C — it uses 2 wires (SDA and SCL) versus 4 for SPI"},
        {"id": "b", "text": "SPI — it is simpler to implement"},
        {"id": "c", "text": "They use the same number of wires"},
        {"id": "d", "text": "UART — it uses only 1 wire"}
    ],
    "correct_option_id": "a",
    "explanation": "I2C uses only SDA+SCL, supporting multiple devices on the same bus; SPI is faster but uses 4 wires per device.",
    "role_families": ["embedded", "iot"],
    "domains": ["embedded"],
    "topic": "protocols",
    "difficulty": "medium",
    "skill_tag": "i2c_vs_spi",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "iot_mqtt_qos_01",
    "prompt": "In MQTT, what does QoS level 1 guarantee?",
    "options": [
        {"id": "a", "text": "The message is delivered at least once (may be delivered more than once)"},
        {"id": "b", "text": "The message is delivered exactly once"},
        {"id": "c", "text": "The message is delivered at most once (fire and forget)"},
        {"id": "d", "text": "The message is encrypted end-to-end"}
    ],
    "correct_option_id": "a",
    "explanation": "QoS 0 = at most once, QoS 1 = at least once, QoS 2 = exactly once.",
    "role_families": ["iot", "embedded"],
    "domains": ["embedded"],
    "topic": "sensors",
    "difficulty": "medium",
    "skill_tag": "mqtt_qos",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "vlsi_setup_hold_01",
    "prompt": "In digital design, a setup time violation occurs when:",
    "options": [
        {"id": "a", "text": "Data arrives at a flip-flop too late before the clock edge"},
        {"id": "b", "text": "Data changes too soon after the clock edge"},
        {"id": "c", "text": "The clock frequency is too low"},
        {"id": "d", "text": "Power supply drops below threshold"}
    ],
    "correct_option_id": "a",
    "explanation": "Setup time is the minimum time data must be stable before the clock edge. Arriving late causes a setup violation.",
    "role_families": ["vlsi"],
    "domains": ["hardware"],
    "topic": "timing",
    "difficulty": "medium",
    "skill_tag": "setup_time",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "robotics_ros_topic_01",
    "prompt": "In ROS, a topic is best described as:",
    "options": [
        {"id": "a", "text": "A named message bus where publishers send and subscribers receive data asynchronously"},
        {"id": "b", "text": "A service call that requires a response"},
        {"id": "c", "text": "A persistent parameter on the parameter server"},
        {"id": "d", "text": "A physical sensor interface"}
    ],
    "correct_option_id": "a",
    "explanation": "ROS topics implement a publish-subscribe pattern for loosely coupled asynchronous communication between nodes.",
    "role_families": ["robotics"],
    "domains": ["robotics"],
    "topic": "control_systems",
    "difficulty": "easy",
    "skill_tag": "ros_topics",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},

# ===========================================================================
# MOBILE — additional
# ===========================================================================
{
    "question_id": "mobile_state_management_01",
    "prompt": "Why is state management important in mobile app development?",
    "options": [
        {"id": "a", "text": "It provides a predictable way to share data across widgets/screens and keep the UI in sync"},
        {"id": "b", "text": "It replaces the need for an API"},
        {"id": "c", "text": "It compresses app assets automatically"},
        {"id": "d", "text": "It manages App Store submissions"}
    ],
    "correct_option_id": "a",
    "explanation": "Without state management, passing data between deeply nested widgets becomes error-prone and hard to maintain.",
    "role_families": ["mobile"],
    "domains": ["software"],
    "topic": "state_management",
    "difficulty": "easy",
    "skill_tag": "state_management_basics",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "mobile_flutter_widget_01",
    "prompt": "In Flutter, the difference between a StatelessWidget and a StatefulWidget is:",
    "options": [
        {"id": "a", "text": "StatefulWidget can rebuild with new data; StatelessWidget renders once with fixed inputs"},
        {"id": "b", "text": "StatelessWidget is faster in all cases"},
        {"id": "c", "text": "StatefulWidget cannot have child widgets"},
        {"id": "d", "text": "They are identical in Flutter 3"}
    ],
    "correct_option_id": "a",
    "explanation": "A StatefulWidget owns a State object that can call setState() to trigger a rebuild when data changes.",
    "role_families": ["mobile"],
    "domains": ["software"],
    "topic": "mobile",
    "difficulty": "easy",
    "skill_tag": "flutter_stateful_widget",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},

# ===========================================================================
# MONITORING / SRE / NETWORKING — additional
# ===========================================================================
{
    "question_id": "sre_slo_01",
    "prompt": "A Service Level Objective (SLO) defines:",
    "options": [
        {"id": "a", "text": "A target for a specific reliability metric, such as 99.9% availability"},
        {"id": "b", "text": "A contract with monetary penalties"},
        {"id": "c", "text": "The number of on-call engineers required"},
        {"id": "d", "text": "The maximum request payload size"}
    ],
    "correct_option_id": "a",
    "explanation": "SLOs are internal reliability targets (e.g. latency p99 < 200ms, 99.9% uptime) used to guide engineering decisions.",
    "role_families": ["devops", "sre"],
    "domains": ["devops"],
    "topic": "monitoring",
    "difficulty": "medium",
    "skill_tag": "slo",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "networking_tcp_vs_udp_01",
    "prompt": "What is the main trade-off between TCP and UDP?",
    "options": [
        {"id": "a", "text": "TCP guarantees ordered, reliable delivery at the cost of overhead; UDP is faster but may drop packets"},
        {"id": "b", "text": "UDP is encrypted; TCP is not"},
        {"id": "c", "text": "TCP is used only for video; UDP only for web"},
        {"id": "d", "text": "They operate at different network layers"}
    ],
    "correct_option_id": "a",
    "explanation": "TCP adds acknowledgements, retransmission, and ordering. UDP skips these for lower latency in real-time use cases.",
    "role_families": ["software_foundations", "backend", "devops", "security", "embedded"],
    "domains": ["software", "devops"],
    "topic": "networking",
    "difficulty": "easy",
    "skill_tag": "tcp_vs_udp",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "networking_dns_01",
    "prompt": "What does DNS (Domain Name System) do?",
    "options": [
        {"id": "a", "text": "Translates human-readable domain names (e.g. google.com) into IP addresses"},
        {"id": "b", "text": "Encrypts HTTP traffic"},
        {"id": "c", "text": "Routes packets between subnets"},
        {"id": "d", "text": "Allocates IP addresses to devices on a network"}
    ],
    "correct_option_id": "a",
    "explanation": "DNS is the internet's phonebook, converting domain names to numeric IP addresses needed for routing.",
    "role_families": ["software_foundations", "backend", "devops"],
    "domains": ["software", "devops"],
    "topic": "networking",
    "difficulty": "easy",
    "skill_tag": "dns_basics",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},

# ===========================================================================
# ASYNC / EVENT-LOOP — additional
# ===========================================================================
{
    "question_id": "async_async_await_01",
    "prompt": "In Python, what does 'await' do inside an async function?",
    "options": [
        {"id": "a", "text": "Suspends the coroutine until the awaited task completes, allowing other coroutines to run"},
        {"id": "b", "text": "Blocks the entire process until the task completes"},
        {"id": "c", "text": "Spawns a new OS thread"},
        {"id": "d", "text": "Converts the function to a generator"}
    ],
    "correct_option_id": "a",
    "explanation": "await yields control back to the event loop while waiting, enabling concurrency without threads.",
    "role_families": ["backend", "data", "ai_ml"],
    "domains": ["software"],
    "topic": "async_programming",
    "difficulty": "medium",
    "skill_tag": "async_await_python",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "async_callback_hell_01",
    "prompt": "What problem does 'callback hell' refer to in JavaScript?",
    "options": [
        {"id": "a", "text": "Deeply nested callbacks that make code hard to read and maintain"},
        {"id": "b", "text": "Callbacks that run before the DOM loads"},
        {"id": "c", "text": "Using too many event listeners"},
        {"id": "d", "text": "Callbacks that throw exceptions"}
    ],
    "correct_option_id": "a",
    "explanation": "Nesting many callbacks creates a 'pyramid of doom' that Promises and async/await were designed to solve.",
    "role_families": ["frontend", "backend"],
    "domains": ["software"],
    "topic": "async_programming",
    "difficulty": "easy",
    "skill_tag": "callback_hell",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},

# ===========================================================================
# AUTH / SECURITY — additional
# ===========================================================================
{
    "question_id": "auth_oauth2_01",
    "prompt": "In OAuth 2.0, what is the purpose of the Access Token?",
    "options": [
        {"id": "a", "text": "It authorises the client to access protected resources on behalf of the resource owner"},
        {"id": "b", "text": "It stores the user's password securely"},
        {"id": "c", "text": "It encrypts all API responses"},
        {"id": "d", "text": "It replaces the user's session cookie"}
    ],
    "correct_option_id": "a",
    "explanation": "An access token is a credential that grants limited access to a resource server for a specific scope and duration.",
    "role_families": ["backend", "security"],
    "domains": ["software", "security"],
    "topic": "auth",
    "difficulty": "medium",
    "skill_tag": "oauth2_access_token",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
{
    "question_id": "auth_hashing_vs_encryption_01",
    "prompt": "Why are passwords stored as hashes (e.g. bcrypt) rather than encrypted?",
    "options": [
        {"id": "a", "text": "Hashing is one-way and irreversible; even if the database leaks, passwords cannot be recovered"},
        {"id": "b", "text": "Hashing is faster to compute than encryption"},
        {"id": "c", "text": "Encrypted passwords require a special database"},
        {"id": "d", "text": "Hashing compresses the password to save space"}
    ],
    "correct_option_id": "a",
    "explanation": "One-way hashing means there is no decryption key. Bcrypt also adds a salt and deliberate slowness against brute-force.",
    "role_families": ["backend", "security"],
    "domains": ["software", "security"],
    "topic": "auth",
    "difficulty": "medium",
    "skill_tag": "password_hashing",
    "question_type": "mcq_single",
    "is_core": False,
    "active": True
},
]

# ---------------------------------------------------------------------------
def main() -> None:
    data = json.loads(BANK_PATH.read_text(encoding="utf-8"))
    existing_ids = {q["question_id"] for q in data["questions"]}

    added = 0
    skipped = 0
    for q in NEW_QUESTIONS:
        if q["question_id"] in existing_ids:
            skipped += 1
            continue
        data["questions"].append(q)
        existing_ids.add(q["question_id"])
        added += 1

    BANK_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Done. Added {added} questions, skipped {skipped} duplicates.")
    print(f"Total questions: {len(data['questions'])}")

    # Summary
    from collections import Counter
    qs = data["questions"]
    print("Difficulty:", dict(Counter(x["difficulty"] for x in qs)))
    print("Type:      ", dict(Counter(x["question_type"] for x in qs)))

if __name__ == "__main__":
    main()
