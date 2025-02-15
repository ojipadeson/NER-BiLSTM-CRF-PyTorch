# coding=utf-8
import optparse
import itertools
import sys
import torch
import pickle

from collections import OrderedDict
from tqdm import tqdm
from torch.autograd import Variable

import loader
from utils import *
from loader import *
from model import BiLSTM_CRF

optparser = optparse.OptionParser()
optparser.add_option("-T", "--train", default="data/eng.train", help="Train set location")
optparser.add_option("-d", "--dev", default="data/eng.testa", help="Dev set location")
optparser.add_option("-t", "--test", default="data/eng.testb", help="Test set location")
optparser.add_option("--test_train", default="data/eng.train50000", help="test train")
optparser.add_option("--score", default="evaluation/temp/score.txt", help="score file location")
optparser.add_option("-s", "--tag_scheme", default="iobes", help="Tagging scheme (IOB or IOBES)")
optparser.add_option(
    "-l",
    "--lower",
    default="1",
    type="int",
    help="Lowercase words (this will not affect character inputs)",
)
optparser.add_option("-z", "--zeros", default="0", type="int", help="Replace digits with 0")
optparser.add_option("-c", "--char_dim", default="25", type="int", help="Char embedding dimension")
optparser.add_option(
    "-C",
    "--char_lstm_dim",
    default="25",
    type="int",
    help="Char LSTM hidden layer size",
)
optparser.add_option(
    "-b",
    "--char_bidirect",
    default="1",
    type="int",
    help="Use a bidirectional LSTM for chars",
)
optparser.add_option("-w", "--word_dim", default="300", type="int", help="Token embedding dimension")
optparser.add_option(
    "-W",
    "--word_lstm_dim",
    default="200",
    type="int",
    help="Token LSTM hidden layer size",
)
optparser.add_option(
    "-B",
    "--word_bidirect",
    default="1",
    type="int",
    help="Use a bidirectional LSTM for words",
)
optparser.add_option(
    "-p",
    "--pre_emb",
    default="glove.840B.300d.txt",
    help="Location of pretrained embeddings",
)
optparser.add_option("-A", "--all_emb", default="1", type="int", help="Load all embeddings")
optparser.add_option(
    "-a",
    "--cap_dim",
    default="0",
    type="int",
    help="Capitalization feature dimension (0 to disable)",
)
optparser.add_option("-f", "--crf", default="1", type="int", help="Use CRF (0 to disable)")
optparser.add_option(
    "-D",
    "--dropout",
    default="0.5",
    type="float",
    help="Droupout on the input (0 = no dropout)",
)
optparser.add_option("-r", "--reload", default="0", type="int", help="Reload the last saved model")
optparser.add_option("-g", "--use_gpu", default="1", type="int", help="whether or not to ues gpu")
optparser.add_option("--loss", default="loss.txt", help="loss file location")
optparser.add_option("--name", default="test", help="model name")
optparser.add_option("--char_mode", choices=["CNN", "LSTM"], default="CNN", help="char_CNN or char_LSTM")
opts = optparser.parse_args()[0]

parameters = OrderedDict()
parameters["tag_scheme"] = opts.tag_scheme
parameters["lower"] = opts.lower == 1
parameters["zeros"] = opts.zeros == 1
parameters["char_dim"] = opts.char_dim
parameters["char_lstm_dim"] = opts.char_lstm_dim
parameters["char_bidirect"] = opts.char_bidirect == 1
parameters["word_dim"] = opts.word_dim
parameters["word_lstm_dim"] = opts.word_lstm_dim
parameters["word_bidirect"] = opts.word_bidirect == 1
parameters["pre_emb"] = opts.pre_emb
parameters["all_emb"] = opts.all_emb == 1
parameters["cap_dim"] = opts.cap_dim
parameters["crf"] = opts.crf == 1
parameters["dropout"] = opts.dropout
parameters["reload"] = opts.reload == 1
parameters["name"] = opts.name
parameters["char_mode"] = opts.char_mode
parameters["use_gpu"] = opts.use_gpu == 1 and torch.cuda.is_available()

use_gpu = parameters["use_gpu"]
device = torch.device("cuda" if use_gpu else "cpu")

name = parameters["name"]
mapping_file = "models/mapping.pkl"
models_path = "models/"
model_name = models_path + name  # get_name(parameters)
tmp_model = model_name + ".tmp"


def evaluating(model_to_evaluate, datas, best_F):
    # FB1 on pharse level
    prediction = []
    save = False
    new_F = 0.0
    confusion_matrix = torch.zeros((len(tag_to_id) - 2, len(tag_to_id) - 2))
    for data in datas:
        ground_truth_id = data['tags']
        words = data['str_words']
        chars2 = data['chars']
        caps = data['caps']

        if parameters['char_mode'] == 'LSTM':
            chars2_sorted = sorted(chars2, key=lambda p: len(p), reverse=True)
            d = {}
            for i, ci in enumerate(chars2):
                for j, cj in enumerate(chars2_sorted):
                    if ci == cj and j not in d and i not in d.values():
                        d[j] = i
                        continue
            chars2_length = [len(c) for c in chars2_sorted]
            char_maxl = max(chars2_length)
            chars2_mask = np.zeros((len(chars2_sorted), char_maxl), dtype='int')
            for i, c in enumerate(chars2_sorted):
                chars2_mask[i, :chars2_length[i]] = c
            chars2_mask = Variable(torch.LongTensor(chars2_mask))

        d = None
        if parameters['char_mode'] == 'CNN':
            d = {}
            chars2_length = [len(c) for c in chars2]
            char_maxl = max(chars2_length)
            chars2_mask = np.zeros((len(chars2_length), char_maxl), dtype='int')
            for i, c in enumerate(chars2):
                chars2_mask[i, :chars2_length[i]] = c

            chars2_mask = Variable(torch.LongTensor(chars2_mask))

        dwords = Variable(torch.LongTensor(data['words']))
        dcaps = Variable(torch.LongTensor(caps))
        if use_gpu:
            val, out = model_to_evaluate(dwords.cuda(), chars2_mask.cuda(), dcaps.cuda(), chars2_length, d)
        else:
            val, out = model_to_evaluate(dwords, chars2_mask, dcaps, chars2_length, d)
        predicted_id = out
        for (word, true_id, pred_id) in zip(words, ground_truth_id, predicted_id):
            line = ' '.join([word, id_to_tag[true_id], id_to_tag[pred_id]])
            prediction.append(line)
            confusion_matrix[true_id, pred_id] += 1
        prediction.append('')
    predf = eval_temp + '/pred.' + name
    scoref = eval_temp + '/score.' + name

    with open(predf, 'w') as f:
        f.write('\n'.join(prediction))

    os.system('%s < %s > %s' % (eval_script, predf, scoref))

    eval_lines = [line.rstrip() for line in open(scoref, 'r', encoding='utf8')]

    for i, line in enumerate(eval_lines):
        print(line)
        if i == 1:
            new_F = float(line.strip().split()[-1])
            if new_F > best_F:
                best_F = new_F
                save = True
                print('the best F is ', new_F)

    print(("{: >2}{: >7}{: >7}%s{: >9}" % ("{: >7}" * confusion_matrix.size(0))).format(
        "ID", "NE", "Total",
        *([id_to_tag[i] for i in range(confusion_matrix.size(0))] + ["Percent"])
    ))
    for i in range(confusion_matrix.size(0)):
        print(("{: >2}{: >7}{: >7}%s{: >9}" % ("{: >7}" * confusion_matrix.size(0))).format(
            str(i), id_to_tag[i], str(confusion_matrix[i].sum().item()),
            *([confusion_matrix[i][j] for j in range(confusion_matrix.size(0))] +
              ["%.3f" % (confusion_matrix[i][i] * 100. / max(1, confusion_matrix[i].sum()))])
        ))
    print()
    return best_F, new_F, save


def train():
    learning_rate = 0.015
    optimizer = torch.optim.SGD(model.parameters(), lr=learning_rate, momentum=0.9)
    loss = 0.0
    best_dev_F = -1.0
    best_test_F = -1.0
    best_train_F = -1.0
    all_F = [[0, 0, 0]]
    count = 0
    sys.stdout.flush()

    model.train(True)

    for epoch in range(1, 10001):
        for iter_num, index in enumerate(tqdm(np.random.permutation(len(train_data)))):
            data = train_data[index]
            model.zero_grad()
            count += 1
            sentence_in = data["words"]
            sentence_in = Variable(torch.LongTensor(sentence_in))
            tags = data["tags"]
            chars = data["chars"]

            # char lstm
            if parameters["char_mode"] == "LSTM":
                chars_sorted = sorted(chars, key=lambda p: len(p), reverse=True)
                d = {}
                for i, ci in enumerate(chars):
                    for j, cj in enumerate(chars_sorted):
                        if ci == cj and j not in d and i not in d.values():
                            d[j] = i
                            continue
                chars_length = [len(c) for c in chars_sorted]
                char_maxl = max(chars_length)
                chars_mask = np.zeros((len(chars_sorted), char_maxl), dtype="int")
                for i, c in enumerate(chars_sorted):
                    chars_mask[i, :chars_length[i]] = c
                chars_mask = Variable(torch.LongTensor(chars_mask))

            # char cnn
            d = None
            if parameters["char_mode"] == "CNN":
                d = {}
                chars_length = [len(c) for c in chars]
                char_maxl = max(chars_length)
                chars_mask = np.zeros((len(chars_length), char_maxl), dtype="int")
                for i, c in enumerate(chars):
                    chars_mask[i, :chars_length[i]] = c
                chars_mask = Variable(torch.LongTensor(chars_mask))

            targets = torch.LongTensor(tags)
            caps = Variable(torch.LongTensor(data["caps"]))
            if use_gpu:
                neg_log_likelihood = model.neg_log_likelihood(
                    sentence_in.cuda(),
                    targets.cuda(),
                    chars_mask.cuda(),
                    caps.cuda(),
                    chars_length,
                    d,
                )
            else:
                neg_log_likelihood = model.neg_log_likelihood(sentence_in, targets, chars_mask, caps, chars_length, d)
            loss += neg_log_likelihood.data.item() / len(data["words"])
            neg_log_likelihood.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            optimizer.step()

            if count % len(train_data) == 0:
                adjust_learning_rate(optimizer, lr=learning_rate / (1 + 0.05 * count / len(train_data)))

        model.train(False)
        best_train_F, new_train_F, _ = evaluating(model, test_train_data, best_train_F)
        best_dev_F, new_dev_F, save = evaluating(model, dev_data, best_dev_F)
        if save:
            torch.save(model, model_name)
        best_test_F, new_test_F, _ = evaluating(model, test_data, best_test_F)
        sys.stdout.flush()

        all_F.append([new_train_F, new_dev_F, new_test_F])
        model.train(True)


if __name__ == "__main__":
    assert os.path.isfile(opts.train)
    assert os.path.isfile(opts.dev)
    assert os.path.isfile(opts.test)
    assert parameters["char_dim"] > 0 or parameters["word_dim"] > 0
    assert 0.0 <= parameters["dropout"] < 1.0
    assert parameters["tag_scheme"] in ["iob", "iobes"]
    assert not parameters["all_emb"] or parameters["pre_emb"]
    assert not parameters["pre_emb"] or parameters["word_dim"] > 0
    assert not parameters["pre_emb"] or os.path.isfile(parameters["pre_emb"])

    if not os.path.isfile(eval_script):
        raise Exception('CoNLL evaluation script not found at "%s"' % eval_script)
    if not os.path.exists(eval_temp):
        os.makedirs(eval_temp)
    if not os.path.exists(models_path):
        os.makedirs(models_path)

    lower = parameters["lower"]
    zeros = parameters["zeros"]
    tag_scheme = parameters["tag_scheme"]

    train_sentences = loader.load_sentences(opts.train, lower, zeros)
    dev_sentences = loader.load_sentences(opts.dev, lower, zeros)
    test_sentences = loader.load_sentences(opts.test, lower, zeros)
    test_train_sentences = loader.load_sentences(opts.test_train, lower, zeros)

    update_tag_scheme(train_sentences, tag_scheme)
    update_tag_scheme(dev_sentences, tag_scheme)
    update_tag_scheme(test_sentences, tag_scheme)
    update_tag_scheme(test_train_sentences, tag_scheme)

    dico_words_train = word_mapping(train_sentences, lower)[0]

    dico_words, word_to_id, id_to_word = augment_with_pretrained(
        dico_words_train.copy(),
        parameters["pre_emb"],
        list(itertools.chain.from_iterable([[w[0] for w in s] for s in dev_sentences +
                                            test_sentences])) if not parameters["all_emb"] else None,
    )

    dico_chars, char_to_id, id_to_char = char_mapping(train_sentences)
    dico_tags, tag_to_id, id_to_tag = tag_mapping(train_sentences)

    train_data = prepare_dataset(train_sentences, word_to_id, char_to_id, tag_to_id, lower)
    dev_data = prepare_dataset(dev_sentences, word_to_id, char_to_id, tag_to_id, lower)
    test_data = prepare_dataset(test_sentences, word_to_id, char_to_id, tag_to_id, lower)
    test_train_data = prepare_dataset(test_train_sentences, word_to_id, char_to_id, tag_to_id, lower)

    print("%i / %i / %i sentences in train / dev / test." % (len(train_data), len(dev_data), len(test_data)))

    all_word_embeds = {}
    for i_num, emb_line in enumerate(open(opts.pre_emb, "r", encoding="utf-8")):
        s = emb_line.strip().split()
        if len(s) == parameters["word_dim"] + 1:
            all_word_embeds[s[0]] = np.array([float(i_num) for i_num in s[1:]])

    word_embeds = np.random.uniform(-np.sqrt(0.06), np.sqrt(0.06), (len(word_to_id), opts.word_dim))

    for w in word_to_id:
        if w in all_word_embeds:
            word_embeds[word_to_id[w]] = all_word_embeds[w]
        elif w.lower() in all_word_embeds:
            word_embeds[word_to_id[w]] = all_word_embeds[w.lower()]

    print("Loaded %i pretrained embeddings." % len(all_word_embeds))

    with open(mapping_file, "wb") as file:
        mappings = {
            "word_to_id": word_to_id,
            "tag_to_id": tag_to_id,
            "char_to_id": char_to_id,
            "parameters": parameters,
            "word_embeds": word_embeds,
        }
        pickle.dump(mappings, file)

    print("word_to_id: ", len(word_to_id))

    torch.cuda.empty_cache()
    # torch.cuda.memory_summary(device=None, abbreviated=False)

    model = BiLSTM_CRF(
        vocab_size=len(word_to_id),
        tag_to_ix=tag_to_id,
        embedding_dim=parameters["word_dim"],
        hidden_dim=parameters["word_lstm_dim"],
        use_gpu=use_gpu,
        char_to_ix=char_to_id,
        pre_word_embeds=word_embeds,
        use_crf=parameters["crf"],
        char_mode=parameters["char_mode"],
    )
    # n_cap=4,
    # cap_embedding_dim=10)

    if parameters["reload"]:
        model = torch.load(model_name)

    model.to(device)
    train()
