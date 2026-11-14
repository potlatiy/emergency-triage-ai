# Emergency triage with AI (work in progress)

Hackathon project, Health track. The aim: give each emergency patient an urgency level the moment they
describe their problem, so that urgent patients are seen first. Nothing is built yet; this repository
currently holds the data.

*This project is a prototype and is not medical advice.*

## The data

`data/ktas_data.xlsx` (also as `data/ktas_data.csv`): 1,267 adult patients from two emergency departments
in South Korea, 24 columns. Each row has the patient's complaint, vital signs, the urgency level given by
the triage nurse (`KTAS_RN`) and the correct level decided by an expert panel (`KTAS_expert`).
Level 1 is the most urgent and level 5 the least.

`data/ktas_codebook.xlsx` (also as `data/ktas_codebook.csv`): what every column and code means.

## Source and licence

Moon S-H, Shim JL, Park K-S, Park C-S (2019). Triage accuracy and causes of mistriage using the Korean
Triage and Acuity Scale. PLOS ONE 14(9): e0216972. https://doi.org/10.1371/journal.pone.0216972

The data files are the paper's supporting information (S1 and S2), unchanged apart from the file names and
the CSV copies. They are published under the Creative Commons Attribution 4.0 licence (CC BY 4.0).
