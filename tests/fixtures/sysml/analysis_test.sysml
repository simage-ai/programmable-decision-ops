// Source: https://github.com/Systems-Modeling/SysML-v2-Release/blob/fb97b754f29588b8e9c7a35f370880cd15eb29e7/sysml/src/examples/Simple%20Tests/AnalysisTest.sysml
// SysML v2 Release, Eclipse Public License 2.0 (see LICENSE-EPL-2.0.txt and README.md here).
package AnalysisTest {

	part def V {
		m;
	}
	
	part vv : V;
	
	requirement def AnalysisObjective {
		doc /* ... */
	}

	analysis def AnalysisCase {
		subject v : V;
		
		objective obj : AnalysisObjective { 
			subject = result;
		}
		
		v.m
	}
	
	analysis def AnalysisPlan {
		subject v : V;
		
		objective {
			doc /* ... */
		}
		
		analysis analysisCase : AnalysisCase { return mass; }
	}
	
	part analysisContext {
		analysis analysisPlan : AnalysisPlan {
			subject v = vv;
		}
	}
}
