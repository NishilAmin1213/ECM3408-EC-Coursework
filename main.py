import os
import re
import sys
import sqlite3
from flask import Flask, Response, jsonify, request
import requests

'''
NOTES
- CHECK RETURN CODES LOOKING AT SPEC
- do we need to empty firebase at the beginning or will it already be empty
'''

global storage_method, connection, db_name
app = Flask("ECM3408 Flask Server")


class CellNotFoundError(Exception):
    pass


def validate_cell_input(cell):
    return re.match(r"[A-Z]+[0-9]+$", cell)


@app.route('/cells/<cell>', methods=['PUT'])
def update_cell(cell):
    # NOT 204 No Content is never returned from this code, WHY!
    print("in update_cell")
    data = request.json

    def validate_cell_formula_input(url_cell, input_json):
        # the cell must be valid
        # the cell in the JSON must equal the cell in the URL
        # the formula must not be blank
        # both 'id' and 'formula' should be valid keys in 'input_json'
        try:
            return (validate_cell_input(input_json['id']) and
                    url_cell == input_json['id'] and
                    len(input_json['formula']) > 0)
        except KeyError:
            return False

    if not validate_cell_formula_input(cell, data):
        return Response(status=400)

    if storage_method == 'sqlite':
        cursor = connection.cursor()

        if len(cursor.execute("SELECT * FROM SPREADSHEET WHERE id=?", (data['id'],)).fetchall()) == 0:
            # the cell does not already exist, so insert the cell id and formula into the spreadsheet
            cursor.execute("INSERT INTO SPREADSHEET VALUES (?, ?)", (data['id'], data['formula']))
            connection.commit()
            # return status 201 - Created
            return Response(status=201)
        else:
            # the cell already exists, so change the contents of its formula
            cursor.execute('UPDATE SPREADSHEET SET formula=? WHERE id=?', (data['formula'], data['id']))
            connection.commit()
            # return status 204 - No Content
            return Response(status=204)
    elif storage_method == 'firebase':

        return_code = 201
        if cell in get_cells():
            return_code = 204

        response = requests.put(
            url="https://" + db_name + "-default-rtdb.europe-west1.firebasedatabase.app/cells/" + data['id'] + ".json",
            json={"formula": data['formula']})
        if response.status_code == 200:
            return Response(status=return_code)

    # the program should not reach this point, if it does, return code 500 (Internal Server Error)
    return Response(status=500)


def evaluate_formula(formula):
    def evaluate_cell(match):
        # get the CELL from the match object and recurse into this function to evaluate its value
        cell = match.string[match.span()[0]:match.span()[1]]
        return str(evaluate_formula(cell))

    if validate_cell_input(formula):
        # formula is a single cell, so get the cell value from the database and return it
        cell_contents = read_cell(formula)
        if cell_contents.status_code == 200:
            return cell_contents.json['formula']
        else:
            raise CellNotFoundError

    # Replace cell indexes with their values and return the evaluated expression
    clean_formula = re.compile(r'[A-Z]+[0-9]+').sub(evaluate_cell, formula)
    return str(eval(clean_formula))


@app.route('/cells/<cell>', methods=['GET'])
def read_cell(cell):
    print("in read_cell, reading cell: " + str(cell))

    if not validate_cell_input(cell):
        # return code 404 (Not Found)
        return Response(status=404)

    try:
        if storage_method == 'sqlite':
            cursor = connection.cursor()

            query = cursor.execute('SELECT * FROM SPREADSHEET WHERE id=?', (cell,)).fetchall()
            if len(query) == 0:
                # if the length of the query result is 0 then the cell does not exist.
                # return 404 (Not Found)
                return Response(status=404)
            else:
                # as the length of the query result is >0 the cell exists, data is retrieved and formula is evaluated
                # return the id and formula in JSON
                # This returns code 200 (OK) by default
                return jsonify(id=query[0][0], formula=evaluate_formula(query[0][1]))
        elif storage_method == 'firebase':

            response = requests.get(
                url="https://" + db_name + "-default-rtdb.europe-west1.firebasedatabase.app/cells/" + cell + ".json")
            if response.status_code == 200:
                if response.json() == None:
                    return Response(status=404)
                else:
                    # cell exists and json contains the formula
                    return jsonify(id=cell, formula=evaluate_formula(response.json()['formula']))

    except CellNotFoundError:
        # A cell which was contained within a formula did not exist, therefore return 404 (Not Found)
        return Response(status=404)

    # the program should not reach this point, if it does, return code 500 (Internal Server Error)
    return Response(status=500)


@app.route('/cells/<cell>', methods=['DELETE'])
def delete_cell(cell):
    print("in delete_cell")

    if not validate_cell_input(cell):
        # return code 404 (Not Found)
        return Response(status=404)

    if storage_method == 'sqlite':
        cursor = connection.cursor()

        if len(cursor.execute("SELECT * FROM SPREADSHEET WHERE id=?", (cell,)).fetchall()) == 0:
            # the cell does not already exist, return 404
            return Response(status=404)
        else:
            # the cell already exists, so delete it and return 204
            cursor.execute("DELETE FROM SPREADSHEET WHERE id=?", (cell,))
            connection.commit()
            return Response(status=204)
    elif storage_method == 'firebase':

        response = requests.delete(
            url="https://" + db_name + "-default-rtdb.europe-west1.firebasedatabase.app/cells/" + cell + ".json")
        if response.status_code == 200:
            return Response(status=204)

    # the program should not reach this point, if it does, return code 500 (Internal Server Error)
    return Response(status=500)


def get_cells():
    cells = []

    if storage_method == 'sqlite':
        cursor = connection.cursor()
        for cell in cursor.execute("SELECT id FROM SPREADSHEET ").fetchall():
            cells.append(cell[0])
    elif storage_method == 'firebase':
        try:
            response = requests.get(
                url="https://" + db_name + "-default-rtdb.europe-west1.firebasedatabase.app/cells.json")
            return list(response.json().keys())
        except AttributeError:
            return []

    return cells


@app.route('/cells', methods=['GET'])
def list_cells():
    print("in list_cells")
    try:
        # return an array containing the cells that are in the database
        # This returns code 200 (OK) by default
        return jsonify(get_cells())
    except Exception:
        # if we get here, there was an issue retrieving cells, return 500
        return Response(status=500)


def initial_cleanup():
    if os.path.isfile('./mydb.db'):
        os.remove("./mydb.db")


def setup_db():
    connection = sqlite3.connect("./mydb.db", check_same_thread=False)
    cursor = connection.cursor()
    cursor.execute(""" CREATE TABLE SPREADSHEET (
                                id VARCHAR(255) NOT NULL,
                                formula VARCHAR(225) NOT NULL
                        );""")
    connection.commit()
    return connection


def clear_firebase():
    for cell in get_cells():
        delete_cell(cell)


if __name__ == '__main__':
    storage_method = sys.argv[1]
    print("Started Program")

    if storage_method == 'sqlite':
        print("Using Storage Method: " + storage_method + '\n')
        initial_cleanup()
        connection = setup_db()
    elif storage_method == 'firebase':
        try:
            db_name = os.environ['FBASE']
            clear_firebase()
        except KeyError:
            print("ERROR: ENV VAR 'FBASE' DOES NOT EXIST")
            quit()
    else:
        print("INCORRECT STORAGE METHOD GIVEN - ACCEPTS 'sqlite' or 'firebase' ")
        quit()

        print("Using Storage Method: " + storage_method)
        db_url = "https://" + db_name + "-default-rtdb.europe-west1.firebasedatabase.app/cells"
        print("Firebase Database Name: " + db_name + '\n')
        print("Firebase Database URL: " + db_url + '\n')

    app.run(host='localhost', port=3000)
