"""Seed vocabulary for the Backend/SDE, Data/Platform and AI/ML role families.

This is the starting point only. `scripts/gen_vocabulary.py` merges it with
terms harvested from the master resume and writes data/vocabulary.yaml, which
is then hand-editable and is the file the running system actually reads.

Structure per entry:
    key: (Canonical Name, category, [aliases...])

Aliases must be lowercase. Matching is longest-alias-first, so "spring boot"
wins over "spring" and "data structures" over "data".
"""

from __future__ import annotations

# category: language | framework | database | tool | platform | concept | practice
SEED: dict[str, tuple[str, str, list[str]]] = {
    # ---------------- languages ----------------
    "java": ("Java", "language", ["java", "core java", "java se", "java 8", "java 11", "java 17"]),
    "python": ("Python", "language", ["python", "python3", "py"]),
    "cpp": ("C++", "language", ["c++", "cpp", "c plus plus"]),
    "c": ("C", "language", ["c language", "ansi c"]),
    "sql": ("SQL", "language", ["sql", "ansi sql"]),
    "javascript": ("JavaScript", "language", ["javascript", "js", "es6", "ecmascript"]),
    "typescript": ("TypeScript", "language", ["typescript", "ts"]),
    "go": ("Go", "language", ["golang", "go lang"]),
    "scala": ("Scala", "language", ["scala"]),
    "kotlin": ("Kotlin", "language", ["kotlin"]),
    "rust": ("Rust", "language", ["rust"]),
    "csharp": ("C#", "language", ["c#", "csharp", "c sharp", ".net", "dotnet"]),
    "bash": ("Shell Scripting", "language", ["bash", "shell scripting", "shell script", "sh"]),

    # ---------------- backend frameworks ----------------
    "spring_boot": ("Spring Boot", "framework", ["spring boot", "springboot", "spring-boot"]),
    "spring": ("Spring Framework", "framework", ["spring framework", "spring mvc", "spring"]),
    "hibernate": ("Hibernate", "framework", ["hibernate", "jpa", "java persistence api"]),
    "fastapi": ("FastAPI", "framework", ["fastapi", "fast api"]),
    "flask": ("Flask", "framework", ["flask"]),
    "django": ("Django", "framework", ["django"]),
    "node": ("Node.js", "framework", ["node.js", "nodejs", "node js", "node"]),
    "express": ("Express", "framework", ["express.js", "expressjs", "express"]),

    # ---------------- api / architecture ----------------
    "rest": ("REST APIs", "concept", ["rest api", "rest apis", "restful", "rest", "restful api"]),
    "graphql": ("GraphQL", "concept", ["graphql"]),
    "grpc": ("gRPC", "concept", ["grpc"]),
    "microservices": ("Microservices", "concept", ["microservice", "microservices", "micro service"]),
    "event_driven": ("Event-Driven Architecture", "concept",
                     ["event-driven architecture", "event driven architecture",
                      "event-driven", "event driven", "eda"]),
    "distributed_systems": ("Distributed Systems", "concept",
                            ["distributed systems", "distributed system", "distributed computing"]),
    "system_design": ("System Design", "concept", ["system design", "software design", "hld", "lld"]),
    "scalability": ("Scalability", "concept", ["scalability", "scalable", "scaling"]),
    # "asynchronous" must be an alias alongside "async": the resume says
    # "asynchronous event processing" while a rewrite says "async", and without
    # both forms the whitelist cannot trace one to the other and flags a
    # perfectly supported claim.
    "concurrency": ("Concurrency", "concept",
                    ["concurrency", "concurrent", "multithreading",
                     "multi-threading", "threading", "async", "asynchronous",
                     "asynchronously", "parallelism"]),
    "caching": ("Caching", "concept", ["caching", "cache"]),
    "performance": ("Performance Optimization", "concept",
                    ["performance optimization", "performance optimisation",
                     "performance tuning", "profiling", "latency optimization",
                     "throughput"]),
    "rdbms": ("Relational Databases", "database",
              ["relational database", "relational databases", "rdbms",
               "relational data"]),
    "nosql": ("NoSQL", "database", ["nosql", "no sql", "non-relational"]),

    # ---------------- databases ----------------
    "postgresql": ("PostgreSQL", "database", ["postgresql", "postgres", "psql", "postgre sql"]),
    "mysql": ("MySQL", "database", ["mysql", "my sql"]),
    "redis": ("Redis", "database", ["redis"]),
    "mongodb": ("MongoDB", "database", ["mongodb", "mongo", "mongo db"]),
    "cassandra": ("Cassandra", "database", ["cassandra"]),
    "elasticsearch": ("Elasticsearch", "database", ["elasticsearch", "elastic search", "elk"]),
    "dynamodb": ("DynamoDB", "database", ["dynamodb", "dynamo db"]),
    "sql_optimization": ("SQL Query Optimization", "concept",
                         ["sql query optimization", "query optimization", "query tuning",
                          "indexing", "database optimization"]),

    # ---------------- messaging / streaming ----------------
    "kafka": ("Kafka", "platform", ["kafka", "apache kafka"]),
    "rabbitmq": ("RabbitMQ", "platform", ["rabbitmq", "rabbit mq"]),
    "sqs": ("SQS", "platform", ["sqs", "amazon sqs"]),
    "pubsub": ("Pub/Sub", "concept", ["pub/sub", "pubsub", "publish subscribe"]),

    # ---------------- data / platform ----------------
    "spark": ("Apache Spark", "platform", ["apache spark", "spark"]),
    "pyspark": ("PySpark", "platform", ["pyspark", "py spark"]),
    "hadoop": ("Hadoop", "platform", ["hadoop", "hdfs", "mapreduce", "map reduce"]),
    "airflow": ("Airflow", "tool", ["airflow", "apache airflow"]),
    "etl": ("ETL", "concept", ["etl", "elt", "data pipeline", "data pipelines",
                               "data processing pipeline"]),
    "data_warehouse": ("Data Warehousing", "concept",
                       ["data warehouse", "data warehousing", "snowflake", "redshift", "bigquery"]),
    "data_modeling": ("Data Modeling", "concept", ["data modeling", "data modelling", "schema design"]),
    "partitioning": ("Partitioning", "concept", ["partitioning", "partitioned", "sharding"]),

    # ---------------- devops / infra ----------------
    "docker": ("Docker", "tool", ["docker", "containerization", "containers", "containerized"]),
    "kubernetes": ("Kubernetes", "tool", ["kubernetes", "k8s", "kube"]),
    "openshift": ("OpenShift", "platform", ["openshift", "open shift", "red hat openshift"]),
    "jenkins": ("Jenkins", "tool", ["jenkins"]),
    "ci_cd": ("CI/CD", "practice", ["ci/cd", "ci cd", "cicd", "continuous integration",
                                    "continuous delivery", "continuous deployment", "ci"]),
    "git": ("Git", "tool", ["git", "github", "gitlab", "bitbucket", "version control"]),
    "terraform": ("Terraform", "tool", ["terraform", "iac", "infrastructure as code"]),
    "aws": ("AWS", "platform", ["aws", "amazon web services", "ec2", "s3", "lambda"]),
    "gcp": ("GCP", "platform", ["gcp", "google cloud", "google cloud platform"]),
    "azure": ("Azure", "platform", ["azure", "microsoft azure"]),
    "linux": ("Linux", "platform", ["linux", "unix", "ubuntu", "centos"]),

    # ---------------- ai / ml ----------------
    "langchain": ("LangChain", "framework", ["langchain", "lang chain"]),
    "langgraph": ("LangGraph", "framework", ["langgraph", "lang graph"]),
    "vector_db": ("Vector Databases", "database",
                  ["vector database", "vector databases", "vector db", "pinecone",
                   "weaviate", "faiss", "chroma", "qdrant"]),
    "rag": ("RAG", "concept", ["rag", "retrieval augmented generation",
                               "retrieval-augmented generation", "document retrieval"]),
    "llm": ("LLMs", "concept", ["llm", "llms", "large language model",
                                "large language models", "generative ai", "genai"]),
    "nlp": ("NLP", "concept", ["nlp", "natural language processing"]),
    "pytorch": ("PyTorch", "framework", ["pytorch", "torch"]),
    "tensorflow": ("TensorFlow", "framework", ["tensorflow", "tf", "keras"]),
    "sklearn": ("scikit-learn", "framework", ["scikit-learn", "sklearn", "scikit learn"]),
    "pandas": ("pandas", "framework", ["pandas", "numpy"]),
    "machine_learning": ("Machine Learning", "concept", ["machine learning", "ml", "deep learning"]),
    "prompt_engineering": ("Prompt Engineering", "concept", ["prompt engineering", "prompting"]),

    # ---------------- cs fundamentals ----------------
    "dsa": ("Data Structures & Algorithms", "concept",
            ["data structures & algorithms", "data structures and algorithms",
             "data structures", "algorithms", "dsa"]),
    "oop": ("OOP", "concept", ["oop", "oops", "object oriented programming",
                               "object-oriented programming", "object oriented design"]),
    "os": ("Operating Systems", "concept", ["operating systems", "operating system", "os"]),
    "complexity": ("Complexity Analysis", "concept",
                   ["complexity analysis", "time complexity", "space complexity", "big o"]),
    "dbms": ("DBMS", "concept", ["dbms", "database management systems"]),
    "networking": ("Computer Networks", "concept", ["computer networks", "networking", "tcp/ip"]),
    "competitive_programming": ("Competitive Programming", "concept",
                                ["competitive programming", "icpc", "codeforces", "codechef",
                                 "leetcode", "acm icpc"]),

    # ---------------- engineering practice ----------------
    "sdlc": ("SDLC", "practice", ["sdlc", "software development lifecycle",
                                  "software development life cycle"]),
    "agile": ("Agile", "practice", ["agile", "scrum", "kanban", "sprint"]),
    "testing": ("Testing", "practice", ["testing", "unit testing", "integration testing",
                                        "test automation"]),
    "junit": ("JUnit", "tool", ["junit", "testng", "mockito"]),
    "debugging": ("Debugging", "practice", ["debugging", "troubleshooting", "root cause analysis"]),
    "code_quality": ("Code Quality", "practice", ["code quality", "code review", "clean code",
                                                  "refactoring"]),
    "logging": ("Logging & Monitoring", "practice",
                ["logging", "structured logging", "monitoring", "observability",
                 "prometheus", "grafana"]),
    "documentation": ("Documentation", "practice", ["documentation", "technical writing"]),

    # ---------------- recognised but NOT targeted ----------------
    # These are outside the Backend/Data/AI role families and will never be
    # matched on this candidate's resume. They are here on purpose: the
    # anti-hallucination whitelist can only flag a technology it can NAME, so
    # the vocabulary has to recognise common tools the candidate does *not*
    # have. Leaving them out silently weakens the guardrail.
    "selenium": ("Selenium", "tool", ["selenium", "selenium webdriver", "webdriver"]),
    "cypress": ("Cypress", "tool", ["cypress"]),
    "playwright": ("Playwright", "tool", ["playwright"]),
    "appium": ("Appium", "tool", ["appium"]),
    "cucumber": ("Cucumber", "tool", ["cucumber", "gherkin", "bdd"]),
    "jmeter": ("JMeter", "tool", ["jmeter", "load testing", "performance testing"]),
    "postman": ("Postman", "tool", ["postman", "api testing"]),
    "react": ("React", "framework", ["react", "react.js", "reactjs"]),
    "angular": ("Angular", "framework", ["angular", "angularjs"]),
    "vue": ("Vue", "framework", ["vue", "vue.js", "vuejs"]),
    "php": ("PHP", "language", ["php", "laravel"]),
    "ruby": ("Ruby", "language", ["ruby", "rails", "ruby on rails"]),
    "swift": ("Swift", "language", ["swift", "ios"]),
    "android": ("Android", "platform", ["android", "kotlin android"]),
    "salesforce": ("Salesforce", "platform", ["salesforce", "apex"]),
    "sap": ("SAP", "platform", ["sap", "abap"]),
    "tableau": ("Tableau", "tool", ["tableau", "power bi", "powerbi"]),
    "graphite": ("Datadog", "tool", ["datadog", "splunk", "new relic"]),
}

# Holding X is meaningful evidence toward Y, at the given strength.
# Used for Partial credit so a JD asking for "Java" is not scored Missing when
# the candidate has deep Spring Boot work.
IMPLIES: dict[str, list[tuple[str, float]]] = {
    "spring_boot": [("java", 0.9), ("rest", 0.7), ("microservices", 0.5)],
    "spring": [("java", 0.9)],
    "hibernate": [("java", 0.7), ("sql", 0.5)],
    "fastapi": [("python", 0.9), ("rest", 0.8)],
    "flask": [("python", 0.9), ("rest", 0.6)],
    "django": [("python", 0.9)],
    "pyspark": [("python", 0.8), ("spark", 0.95), ("distributed_systems", 0.6), ("etl", 0.7)],
    "spark": [("distributed_systems", 0.6), ("etl", 0.6)],
    "hadoop": [("distributed_systems", 0.6), ("etl", 0.5)],
    "kafka": [("event_driven", 0.8), ("distributed_systems", 0.7), ("pubsub", 0.8)],
    "kubernetes": [("docker", 0.7)],
    "openshift": [("kubernetes", 0.8), ("docker", 0.7)],
    "langchain": [("python", 0.7), ("llm", 0.8), ("rag", 0.6)],
    "langgraph": [("python", 0.7), ("llm", 0.8)],
    "vector_db": [("rag", 0.7)],
    "postgresql": [("sql", 0.9), ("dbms", 0.7), ("rdbms", 0.95)],
    "mysql": [("sql", 0.9), ("dbms", 0.7), ("rdbms", 0.95)],
    "sql_optimization": [("sql", 0.8), ("dbms", 0.6), ("performance", 0.7)],
    "redis": [("caching", 0.8)],
    "ci_cd": [("git", 0.4)],
    "jenkins": [("ci_cd", 0.8)],
    "junit": [("testing", 0.9), ("java", 0.5)],
    "competitive_programming": [("dsa", 0.85), ("complexity", 0.7)],
    "microservices": [("distributed_systems", 0.5), ("rest", 0.5)],
    "event_driven": [("distributed_systems", 0.5)],
}
