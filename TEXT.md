## Pros & Cons of Sub-Agents

### Pros

 - Parallelism: accelerate by working on different components at the same time.
 - Self-correction: keep the project on rails by having agents that check and test.
 - Context effeciency: keep context unpolluted with information related to other tasks
 - Task focus: keep bounded responsibility and prompts to each Agent, reducing context switching

### Cons

 - Orchestratin complexity: more moving parts
 - Boundary issues: mistakes in the dependecies between agents
 - Error amplification: mistake can compound leading to unreliable outcomes..or chaos!
 - Costs: tokens can add up

---

## サブエージェントのメリットとデメリット

### メリット
 - 並列処理：異なるコンポーネントを同時に処理することで高速化できます。
 - 自己修正：エージェントがチェックとテストを行うことで、プロジェクトを軌道に乗せることができます。
 - コンテキスト効率：他のタスクに関連する情報でコンテキストが汚染されるのを防ぎます。
 - タスク集中：各エージェントの責任範囲とプロンプトを限定することで、コンテキストの切り替えを減らします。

### デメリット
 - オーケストレーションの複雑化：構成要素が増えます。
 - 境界の問題：エージェント間の依存関係に誤りがあると、問題が発生しやすくなります。
 - エラーの増幅：エラーが重なり、信頼性の低い結果や混乱を招く可能性があります。
 - コスト：トークンが蓄積される可能性があります。