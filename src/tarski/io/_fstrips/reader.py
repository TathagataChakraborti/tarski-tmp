"""
 The loader imports the AST visitor from the parser directory - this directory is autogenerated by the
 utils/parser appropriate scripts, and should not be manually modified.
"""
import logging

from antlr4 import FileStream, CommonTokenStream, InputStream
from antlr4.error.ErrorListener import ErrorListener
from tarski import model
from tarski.errors import SyntacticError, UndefinedFunction
from tarski.fstrips import DelEffect, AddEffect, FunctionalEffect, UniversalEffect, language
from tarski.syntax import neg, land, lor, Tautology, implies, exists, forall, Atom
from tarski.syntax.visitors import CollectVariables
from tarski.syntax.terms import CompoundTerm, Variable, Constant
from tarski.syntax.builtins import eq, create_atom, BuiltinPredicate

from .parser.visitor import fstripsVisitor
from .parser.lexer import fstripsLexer
from .parser.parser import fstripsParser


class ParsingError(SyntacticError):
    pass


class ExceptionRaiserListener(ErrorListener):
    """ An ANTLR ErrorListener that simply transforms any syntax error into a Tarski parsing error.
        Useful at least for testing purposes.
    """
    def syntaxError(self, recognizer, offending_symbol, line, column, msg, e):
        """ """
        msg = "line " + str(line) + ":" + str(column) + " " + msg
        raise ParsingError(msg)


class FStripsParser(fstripsVisitor):
    """
    The parser assumes that the domain file is visited _before_ the instance file
    """

    def parse_string(self, string, start_rule='pddlDoc'):
        """ Parse a given string starting from a given grammar rule """
        return self._parse_stream(InputStream(string), start_rule)

    def parse_file(self, filename, start_rule='pddlDoc'):
        """ Parse a given filename starting from a given grammar rule """
        return self._parse_stream(FileStream(filename), start_rule)

    def _parse_stream(self, filestream, start_rule='pddlDoc'):
        lexer = self._configure_error_handling(fstripsLexer(filestream))
        stream = CommonTokenStream(lexer)
        parser = self._configure_error_handling(fstripsParser(stream))

        assert hasattr(parser, start_rule)
        tree = getattr(parser, start_rule)()
        return tree, stream

    def _configure_error_handling(self, element):
        if self.error_handler is not None:
            # If necessary, _replace_ previous error handlers with the given one
            element.removeErrorListeners()
            element.addErrorListener(self.error_handler)
        return element

    def __init__(self, problem, raise_on_error=False):
        self.problem = problem
        self.problem.language = language()
        self.problem.init = model.create(problem.language)
        self.error_handler = ExceptionRaiserListener() if raise_on_error else None
        self.declared_variables = None # to keep track of declared variables
        # Shortcuts
        self.language = problem.language
        self.init = self.problem.init

        self.requirements = []

    equivalent_built_in_type = {\
        'number' : 'Real'
    }

    def _translate_to_builtin_type(self, typename):
        try :
            return FStripsParser.equivalent_built_in_type[typename]
        except KeyError :
            return typename

    # TODO GFM NOT REVISED YET

    task_name = None
    task_domain_name = None
    init = []
    goal = None
    constraints = None
    constraint_schemata = []
    type_bounds = []
    objects = []
    metric = None

    axioms = []

    current_params = None

    def visitDomainName(self, ctx):
        self.problem.domain_name = ctx.NAME().getText().lower()

    def visitProblemDecl(self, ctx):
        self.problem.name = ctx.NAME().getText().lower()

    def visitProblemDomain(self, ctx):
        domain_name_as_declared_in_instance = ctx.NAME().getText().lower()
        if domain_name_as_declared_in_instance != self.problem.domain_name:
            logging.warning('Domain names as declared in domain and instance files do not coincide: "{}" vs " {}"'.
                            format(self.problem.domain_name, domain_name_as_declared_in_instance))

    def visitRequireDef(self, ctx):
        for req_ctx in ctx.REQUIRE_KEY():
            self.requirements.append(req_ctx.getText().lower())

    def visitDeclaration_of_types(self, ctx):
        for typename, basename in self.visit(ctx.possibly_typed_name_list()):
            actual_name = self._translate_to_builtin_type(typename)
            if actual_name != typename: # it is a built-in type
                continue
            basename = self._translate_to_builtin_type(basename)
            parents = [self.language.get_sort(basename)]
            self.language.sort(actual_name, parents)

    def extract_namelist(self, ctx):
        return [name.getText().lower() for name in ctx.NAME()]

    def visitSimpleNameList(self, ctx):
        names = self.extract_namelist(ctx)
        return [(name, 'object') for name in names]

    def visitName_list_with_type(self, ctx):
        typename = ctx.typename().getText().lower()
        names = self.extract_namelist(ctx)
        return [(name, self._translate_to_builtin_type(typename)) for name in names]

    def visitComplexNameList(self, ctx):
        simple = self.visitSimpleNameList(ctx)
        derived = []
        for sub in ctx.name_list_with_type():
            derived += self.visit(sub)
        return simple + derived

    def visitSingle_predicate_definition(self, ctx):
        predicate = ctx.predicate().getText().lower()
        argument_types = [a.sort for a in self.visit(ctx.possibly_typed_variable_list())]
        return self.language.predicate(predicate, *argument_types)

    def visitUntypedVariableList(self, ctx):
        variables = [self.language.variable(name.getText().lower(), 'object') for name in ctx.VARIABLE()]
        return variables

    def visitTypedVariableList(self, ctx):
        untyped_var_names = [self.language.variable(name.getText().lower(), 'object') for name in ctx.VARIABLE()]
        typed_var_names = []
        for sub_ctx in ctx.variable_list_with_type():
            typed_var_names += self.visit(sub_ctx)
        return typed_var_names + untyped_var_names

    def visitVariable_list_with_type(self, ctx):
        typename = ctx.primitive_type().getText().lower()  # This is the type of all variables in the list
        return [self.language.variable(name.getText().lower(), self._translate_to_builtin_type(typename)) for name in ctx.VARIABLE()]

    def visitTyped_function_definition(self, ctx, return_type=None):
        return_type = return_type or ctx.primitive_type().getText().lower()
        return_type = self._translate_to_builtin_type(return_type)
        name = ctx.logical_symbol_name().getText().lower()
        argument_types = [a.sort for a in self.visit(ctx.possibly_typed_variable_list())]
        return self.language.function(name, *argument_types, return_type)

    def visitUnTyped_function_definition(self, ctx):
        return self.visitTyped_function_definition(ctx, 'object')

    def visitBoundsDecl(self, ctx):

        for sub_ctx in ctx.typeBoundsDefinition():
            self.type_bounds.append(self.visit(sub_ctx))

    def visitTypeBoundsDefinition(self, ctx):

        b = DomainBound(ctx.NAME().getText().lower(),
                        '{0}[{1}..{2}]'.format(ctx.numericBuiltinType().getText().lower(), ctx.NUMBER(0),
                                               ctx.NUMBER(1)))
        return b

    def visitObject_declaration(self, ctx):
        for o, t in self.visit(ctx.possibly_typed_name_list()):
            # TODO We might want to record elsewhere that these constants are
            # TODO required as per the PDDL spec to have fixed denotation
            self.language.constant(o, t)

    # For a fixed problem, there's no particular distinction btw domain constants and problem objects.
    visitConstant_declaration = visitObject_declaration

    def visitActionDef(self, ctx):
        name = ctx.actionName().getText().lower()
        params = self.visit(ctx.possibly_typed_variable_list())
        self.declared_variables = params
        precondition, effect = self.visit(ctx.actionDefBody())
        self.declared_variables = None
        self.problem.action(name, params, precondition, effect)

    def visitActionDefBody(self, ctx):
        prec = self.visit(ctx.precondition())
        eff = self.visit(ctx.effect())
        return prec, eff

    def visitTrivialPrecondition(self, ctx):
        return Tautology()

    def visitRegularPrecondition(self, ctx):
        return self.visit(ctx.goalDesc())

    def visitAtomicTermFormula(self, ctx):
        predicate_symbol = ctx.predicate().getText().lower()
        predicate = self.language.get_predicate(predicate_symbol)
        subterms = [self.visit(term_ctx) for term_ctx in ctx.term()]
        return predicate(*subterms)

    def visitTermGoalDesc(self, ctx):
        return self.visit(ctx.atomicTermFormula())

    def visitTermObject(self, ctx):
        name = ctx.NAME().getText().lower()
        return self.language.get_constant(name)

    def __process_numeric_literal(self, txt):
        try:
            x = int(txt)
            return self.language.Constant(x,self.language.Integer)
        except ValueError:
            y = float(txt)
            return self.language.Constant(y,self.language.Real)

    def visitTermNumber(self, ctx):
        object_name = ctx.NUMBER().getText().lower()
        return self.__process_numeric_literal(object_name)

    def visitTermVariable(self, ctx):
        variable_name = ctx.VARIABLE().getText().lower()
        if self.declared_variables is None:
            return variable_name
        for variable in self.declared_variables:
            if variable.symbol == variable_name:
                return variable
        raise UnresolvedVariableError(variable_name)

    def visitGenericFunctionTerm(self, ctx):
        func_name = ctx.logical_symbol_name().getText().lower()
        try:
            func = self.language.get_function(func_name)
        except UndefinedFunction as e:
            raise SyntacticError("Undefined function '{}' in term {}".format(func_name,ctx.getText()))

        term_list = []
        for term_ctx in ctx.term():
            term_list.append(self.visit(term_ctx))
        return self.language.CompoundTerm(func, term_list)

    def visitBinaryArithmeticFunctionTerm(self, ctx):
        func_name = ctx.binaryOp().getText().lower()

        term_list = []
        for term_ctx in ctx.term():
            term_list.append(self.visit(term_ctx))

        if len(term_list) != 2:
            raise SyntacticError("Arithmetic function {} arity is 2, arity of expression is {}".format(func_name, len(term_list)))

        try :
            signature = [func_name]
            for a in term_list:
                signature.append(a.sort)
            func = self.language.get_function(tuple(signature))
        except UndefinedFunction as e:
            raise SyntacticError("Function {} was not declared!\n Exception thrown by FirstOrderLanguage.get_function():\n{}".format(func_name, str(e)))

        return self.language.CompoundTerm(func, term_list)

    def visitUnaryArithmeticFunctionTerm(self, ctx):
        func_name = ctx.unaryBuiltIn().getText().lower()
        if func_name not in built_in_functional_symbols:
            raise SystemExit("Function {0} first seen used as a term in an atomic formula".format(func_name))
        if func_name == '-':
            return FunctionalTerm('*', [self.visit(ctx.term()), NumericConstant(-1)])
        return FunctionalTerm(func_name, [self.visit(ctx.term())])

    def visitAndGoalDesc(self, ctx):
        conjuncts = [self.visit(sub_ctx) for sub_ctx in ctx.goalDesc()]
        return land(*conjuncts)

    def visitOrGoalDesc(self, ctx):
        conjuncts = [self.visit(sub_ctx) for sub_ctx in ctx.goalDesc()]
        return lor(*conjuncts)

    def visitNotGoalDesc(self, ctx):
        return neg(self.visit(ctx.goalDesc()))

    def visitImplyGoalDesc(self, ctx):
        lhs = self.visit(ctx.goalDesc(0))
        rhs = self.visit(ctx.goalDesc(1))
        return implies(lhs, rhs)

    def visitExistentialGoalDesc(self, ctx):
        variables = self.visit(ctx.possibly_typed_variable_list())
        # MRJ: so we know what variables have been declared
        if self.declared_variables is None :
            self.declared_variables = variables
        else :
            self.declared_variables += variables
        formula = self.visit(ctx.goalDesc())
        return exists(*variables, formula)

    def visitUniversalGoalDesc(self, ctx):
        variables = self.visit(ctx.possibly_typed_variable_list())
        if self.declared_variables is None :
            self.declared_variables = variables
        else :
            self.declared_variables += variables
        formula = self.visit(ctx.goalDesc())
        return forall(*variables, formula)

    def visitComparisonGoalDesc(self, ctx):
        return self.visit(ctx.fComp())

    def visitEquality(self, ctx):
        lhs = self.visit(ctx.term(0))
        rhs = self.visit(ctx.term(1))
        return eq(lhs, rhs)

    def visitFComp(self, ctx):
        op = ctx.binaryComp().getText().lower()
        neg_op = {'<': '>=', '>': '<=', '<=': '>', '>=': '<'}
        lhs = self.visit(ctx.fExp(0))
        rhs = self.visit(ctx.fExp(1))
        return create_atom(BuiltinPredicate(op), lhs, rhs)

    def visitNumericConstantExpr(self, ctx):
        object_name = ctx.NUMBER().getText().lower()
        return self.__process_numeric_literal(object_name)

    def visitBinaryOperationExpr(self, ctx):
        ## TODO REVISE
        op = ctx.binaryOp().getText().lower()
        lhs = self.visit(ctx.fExp(0))
        rhs = self.visit(ctx.fExp(1))
        return FunctionalTerm(op, [lhs, rhs])

    def visitUnaryOperationExpr(self, ctx):
        ## TODO REVISE
        op = '*'
        lhs = self.visit(ctx.fExp())
        rhs = NumericConstant(-1)
        return PrimitiveNumericExpression(op, [lhs, rhs])

    def visitFunctionExpr(self, ctx):
        return self.visit(ctx.functionTerm())

    def visitVariableExpr(self, ctx):
        variable_name = ctx.VARIABLE().getText().lower()
        if self.declared_variables is None: return variable_name
        for var_obj in self.declared_variables:
            if var_obj.name == variable_name:
                return var_obj
        raise UnresolvedVariableError(variable_name)

    def visitGoal(self, ctx):
        self.problem.goal = self.visit(ctx.goalDesc())

    def visitSingleEffect(self, ctx):
        # The effect might already be a list if it derives from a multiple conditional effect.
        # Otherwise, we turn it into a list
        effect = self.visit(ctx.single_effect())
        return effect if isinstance(effect, list) else [effect]

    def visitConjunctiveEffectFormula(self, ctx):
        return [self.visit(sub_ctx) for sub_ctx in ctx.single_effect()]

    def visitAtomicEffect(self, ctx):
        return self.visit(ctx.atomic_effect())

    def visitAddAtomEffect(self, ctx):
        return AddEffect(self.visit(ctx.atomicTermFormula()))

    def visitDeleteAtomEffect(self, ctx):
        return DelEffect(self.visit(ctx.atomicTermFormula()))

    def visitAssignConstant(self, ctx):
        return FunctionalEffect(self.visit(ctx.functionTerm()), self.visit(ctx.term()))

    def visitUniversallyQuantifiedEffect(self, ctx):
        return UniversalEffect(self.visit(ctx.possibly_typed_variable_list()), self.visit(ctx.effect()))

    def visitSingleConditionalEffect(self, ctx):
        effect = self.visit(ctx.atomic_effect())
        effect.condition = self.visit(ctx.goalDesc())
        return effect

    def visitMultipleConditionalEffect(self, ctx):
        condition = self.visit(ctx.goalDesc())
        effects = [self.visit(sub_ctx) for sub_ctx in ctx.atomic_effect()]
        for eff in effects:
            eff.condition = condition  # We simply copy the condition in each effect

        return effects

    def visitInit(self, ctx):
        # i.e. simply visit all node children
        for element_ctx in ctx.initEl():
            self.visit(element_ctx)

    def visitInitLiteral(self, ctx):
        return self.visit(ctx.nameLiteral())

    def visitGroundAtomicFormula(self, ctx):
        predicate = self.language.get_predicate(ctx.predicate().getText().lower())
        subterms = [self.visit(term_ctx) for term_ctx in ctx.groundTerm()]
        return predicate, subterms

    def visitInitPositiveLiteral(self, ctx):
        predicate, subterms = self.visit(ctx.groundAtomicFormula())
        self.init.add(predicate, *subterms)

    def visitInitNegativeLiteral(self, ctx):
        # predicate, subterms = self.visit(ctx.groundAtomicFormula())
        # No need to do anything here, as atoms are assumed by default to be false
        pass

    def visitGroundFunctionTerm(self, ctx):
        func_name = ctx.logical_symbol_name().getText().lower()
        if func_name not in self.functions_table:
            raise SystemExit("Function {0} first seen used as a term in Initial State".format(func_name))
        term_list = []
        for term_ctx in ctx.groundTerm():
            term_list.append(self.visit(term_ctx))
        return FunctionalTerm(func_name, term_list)

    def visitGroundTermObject(self, ctx):
        name = ctx.NAME().getText().lower()
        return self.language.get_constant(name)

    def visitGroundTermNumber(self, ctx):
        object_name = ctx.NUMBER().getText().lower()
        try:
            return NumericConstant(int(object_name))
        except ValueError:
            return NumericConstant(float(object_name))

    def visitInitAssignmentNumeric(self, ctx):
        lhs = self.visit(ctx.groundFunctionTerm())
        try:
            rhs = NumericConstant(int(ctx.NUMBER().getText().lower()))
        except ValueError:
            rhs = NumericConstant(float(ctx.NUMBER().getText().lower()))
        return Assign(lhs, rhs)

    def visitInitAssignmentObject(self, ctx):
        lhs = self.visit(ctx.groundFunctionTerm())
        obj_name = ctx.NAME().getText().lower()
        if not obj_name in self.objects_table:
            raise SystemExit(
                "Object {0} first seen assigning a value to {1} in the initial state".format(obj_name, str(lhs)))
        rhs = self.objects_table[obj_name]
        return Assign(lhs, rhs)

    def visitExtensionalConstraintGD(self, ctx):
        arg_list = []
        for fn_ctx in ctx.groundFunctionTerm():
            arg_list.append(self.visit(fn_ctx))
        return [Atom(ctx.EXTNAME().getText().lower(), arg_list)]

    def visitAlternativeAlwaysConstraint(self, ctx):
        return [self.visit(ctx.goalDesc())]

    def visitConjunctionOfConstraints(self, ctx):
        constraints = []
        for conGD_ctx in ctx.prefConGD():
            constraints += self.visit(conGD_ctx)
        return constraints

    def visitPlainConstraintList(self, ctx):
        constraints = []
        for conGD_ctx in ctx.conGD():
            constraints += self.visit(conGD_ctx)
        return constraints

    def visitProbConstraints(self, ctx):
        self.constraints = Conjunction(self.visit(ctx.prefConGD()))

    def visitProblemMetric(self, ctx):
        optimization = ctx.optimization().getText().lower()
        self.metric = Metric(optimization)
        self.metric.expr = self.visit(ctx.metricFExp())

    def visitFunctionalExprMetric(self, ctx):
        return None, self.visit(ctx.functionTerm())

    def visitCompositeMetric(self, ctx):
        return self.visit(ctx.terminalCost()), self.visit(ctx.stageCost())

    def visitTerminalCost(self, ctx):
        return self.visit(ctx.functionTerm())

    def visitStageCost(self, ctx):
        return self.visit(ctx.functionTerm())

    def visitTotalTimeMetric(self, ctx):
        raise SystemExit("Unsupported feature: Minimize total-time metric is not supported")

    def visitIsViolatedMetric(self, ctx):
        raise SystemExit("Unsupported feature: Count of violated constraints metric is not supported")

    ########## PROCESS STUFF - UNREVISED #######

    def visitProcessAssignEffect(self, ctx):
        operation = ctx.processEffectOp().getText().lower()
        lhs = self.visit(ctx.functionTerm())
        rhs = self.visit(ctx.processEffectExp())
        if operation in ['assign', 'scale-up', 'scale-down']:
            raise SystemExit("Assign/scale up/scale down effects not allowed in processes")
        trans_op = {'increase': '+', 'decrease': '-'}
        new_rhs = FunctionalTerm(trans_op[operation], [lhs, rhs])
        return AssignmentEffect(lhs, new_rhs)  # This effectively normalizes effects

    def visitFunctionalProcessEffectExpr(self, ctx):
        return self.visit(ctx.processFunctionEff())

    def visitConstProcessEffectExpr(self, ctx):
        return self.visit(ctx.processConstEff())

    def visitVariableProcessEffectExpr(self, ctx):
        return self.visit(ctx.processVarEff())

    def visitProcessFunctionEff(self, ctx):
        return self.visit(ctx.functionTerm())

    def visitProcessConstEff(self, ctx):
        try:
            return NumericConstant(int(ctx.NUMBER().getText().lower()))
        except ValueError:
            return NumericConstant(float(ctx.NUMBER().getText().lower()))

    def visitProcessVarEff(self, ctx):
        variable_name = ctx.VARIABLE().getText().lower()
        if self.declared_variables is None: return variable_name
        for var_obj in self.declared_variables:
            if var_obj.name == variable_name:
                return var_obj
        raise UnresolvedVariableError(variable_name)

    def visitProcessSingleEffect(self, ctx):
        return ConjunctiveEffect([self.visit(ctx.processEffect())])

    def visitProcessConjunctiveEffectFormula(self, ctx):
        effects = []
        for sub_ctx in ctx.processEffect():
            effects.append(self.visit(sub_ctx))
        return ConjunctiveEffect(effects)
        # return self.visitConjunctiveEffectFormula( ctx )

    def visitProcessDef(self, ctx):
        name = ctx.actionName().getText().lower()
        params = self.visit(ctx.possibly_typed_variable_list())
        self.declared_variables = params
        try:
            precondition, effect = self.visit(ctx.processDefBody())
        except UndeclaredVariable as error:
            raise SystemExit("Parsing process {}: undeclared variable {}".format(name, error))
        self.declared_variables = None
        process = Action(name, params, len(params), precondition, effect, None)
        self.processes.append(process)
        # print( 'Action: {0}'.format(name) )
        # print( 'Parameters: {0}'.format(len(params)))
        # for parm in params :
        #    print(parm)
        # precondition.dump()
        # effect.dump()

    def visitProcessDefBody(self, ctx):
        try:
            prec = self.visit(ctx.precondition())
        except UnresolvedVariableError as e:
            raise UndeclaredVariable('precondition', str(e))
        try:
            unnorm_eff = self.visit(ctx.processEffectList())
        except UnresolvedVariableError as e:
            raise UndeclaredVariable('effect', str(e))
        norm_eff = unnorm_eff.normalize()
        norm_eff_list = []
        add_effect(norm_eff, norm_eff_list)

        return prec, norm_eff_list

    def visitAssignEffect(self, ctx):
        operation = ctx.assignOp().getText().lower()
        lhs = self.visit(ctx.functionTerm())
        rhs = self.visit(ctx.fExp())
        if operation == 'assign':
            return AssignmentEffect(lhs, rhs)
        trans_op = {'scale-up': '*', 'scale-down': '/', 'increase': '+', 'decrease': '-'}
        # print("{} {} {}".format( trans_op[operation], lhs, rhs))
        new_rhs = FunctionalTerm(trans_op[operation], [lhs, rhs])
        return AssignmentEffect(lhs, new_rhs)  # This effectively normalizes effects

    def visitEventDef(self, ctx):
        name = ctx.eventSymbol().getText().lower()
        params = self.visit(ctx.possibly_typed_variable_list())
        self.declared_variables = params
        try:
            precondition, effect = self.visit(ctx.actionDefBody())
        except UndeclaredVariable as error:
            raise SystemExit("Parsing event {}: undeclared variable {}".format(name, error))
        self.declared_variables = None
        evt = Event(name, params, len(params), precondition, effect)
        self.events.append(evt)
        # print( 'Action: {0}'.format(name) )
        # print( 'Parameters: {0}'.format(len(params)))
        # for parm in params :
        #    print(parm)
        # precondition.dump()
        # effect.dump()

    def visitConstraintDef(self, ctx):
        name = ctx.constraintSymbol().getText().lower()
        params = self.visit(ctx.possibly_typed_variable_list())
        self.declared_variables = params

        try:
            conditions = self.visit(ctx.goalDesc())
        except UndeclaredVariable as error:
            raise ParsingError(
                "Parsing state constraint {}: undeclared variable:\n {}".format( name, str(error)))
        print(params)
        print(conditions)
        if len(params) > 0 :
            visitor = CollectVariables(self.language)
            conditions.accept(visitor)
            for x in params :
                if not x in visitor.variables:
                    raise ParsingError("Parsing state constraint {}: variable in :parameters not found in constraint formula {}".format(name,x,conditions))
            self.problem.constraints.append(forall( *params, conditions ))
        else :
            self.problem.constraints.append(conditions)
        self.declared_variables = None



class UnresolvedVariableError(Exception):
    def __init__(self, value):
        self.value = value

    def __str__(self):
        return repr(self.value)


class UndeclaredVariable(Exception):
    def __init__(self, component, value):
        self.component = component
        self.value = value

    def __str__(self):
        return 'in {} found undeclared variable {}'.format(self.component, repr(self.value))
