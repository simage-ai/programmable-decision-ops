// Source: https://github.com/Systems-Modeling/SysML-v2-Release/blob/fb97b754f29588b8e9c7a35f370880cd15eb29e7/sysml/src/examples/Simple%20Tests/VariabilityTest.sysml
// SysML v2 Release, Eclipse Public License 2.0 (see LICENSE-EPL-2.0.txt and README.md here).
package VariabilityTest {
	part def P {
		attribute a;
	}
	
	part def Q :> P;
	attribute def B;
	variation part def V :> P {
		variant part x : Q {
			attribute b : B :>> a;
		}
	}
	
	part q : Q;
	variation part v : P {
		variant q {
			attribute b : B :>> a;
		}
	}
	
	part y : P = v::q;
	
	variation action def A {
		variant action a1;
		variant action a2;
	}
	
	variation use case uc1 {
    	variant use case uc11;
    	variant use case uc12;
    }

    variation analysis a1;
    
    variation verification v1;
    
    variation requirement r {
    	variant requirement r1;
    }
	
}
