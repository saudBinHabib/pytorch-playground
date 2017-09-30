import torch
import torch.nn as nn
import torch.optim as optim
from torch.autograd import Variable

import random
import math
import argparse
import itertools
import os

from data import Corpus
import utils
from model import Tagger

parser = argparse.ArgumentParser(description = 'A Joint Many-Task Model')
parser.add_argument('--embedDim', type = int, default = 100,
                    help='Size of word embeddings')
parser.add_argument('--charDim', type = int, default = 100,
                    help='Size of char embeddings')
parser.add_argument('--hiddenDim', type = int, default = 100,
                    help='Size of hidden layers')
parser.add_argument('--batchSize', type = int, default = 32,
                    help='Mini-batch size')
parser.add_argument('--lr', type = float, default = 1.0,
                    help='Initial learning rate')
parser.add_argument('--lrDecay', type = float, default = 0.3,
                    help='Learning rate decay per epoch')
parser.add_argument('--lstmWeightDecay', type = float, default = 1.0e-06,
                    help='Weight decay for LSTM weights')
parser.add_argument('--mlpWeightDecay', type = float, default = 1.0e-05,
                    help='Weight decay for MLP weights')
parser.add_argument('--epoch', type = int, default = 20,
                    help='Maximum number of training epochs')
parser.add_argument('--seed', type = int, default = 1,
                    help='Random seed')
parser.add_argument('--gpuId', type = int, default = 0,
                    help='GPU id')
parser.add_argument('--inputDropout', type = float, default = 0.2,
                    help='Dropout rate for input vectors')
parser.add_argument('--outputDropout', type = float, default = 0.2,
                    help='Dropout rate for output vectors')
parser.add_argument('--clip', type = float, default = 1.0,
                    help='Gradient clipping value')
parser.add_argument('--random', action = 'store_true',
                    help='Use randomly initialized embeddings or not')
parser.add_argument('--test', action = 'store_true',
                    help = 'Test mode or not')

args = parser.parse_args()
print(args)
print()

embedDim = args.embedDim
charDim = args.charDim
hiddenDim = args.hiddenDim
batchSize = args.batchSize
initialLearningRate = args.lr
lrDecay = args.lrDecay
lstmWeightDecay = args.lstmWeightDecay
mlpWeightDecay = args.mlpWeightDecay
maxEpoch = args.epoch
seed = args.seed
inputDropoutRate = args.inputDropout
outputDropoutRate = args.outputDropout
gradClip = args.clip
useGpu = True
gpuId = args.gpuId
test = args.test

trainFile = '../dataset/pos/pos_wsj.sample.train'
devFile = '../dataset/pos/pos_wsj.sample.dev'

wordEmbeddingFile = '../embedding/word.txt'
charEmbeddingFile = '../embedding/charNgram.txt'

modelParamsFile = 'params-'+str(gpuId)
wordParamsFile = 'word_params-'+str(gpuId)
charParamsFile = 'char_params-'+str(gpuId)

torch.manual_seed(seed)
random.seed(seed)

corpus = Corpus(trainFile, devFile)

print('Vocabulary size: '+str(corpus.voc.size()))
print('# of classes:    '+str(corpus.classVoc.size()))
print()
print('# of training samples: '+str(len(corpus.trainData)))
print('# of dev samples:      '+str(len(corpus.devData)))
print()

tagger = Tagger(corpus.voc.size(), corpus.charVoc.size(),
                embedDim, charDim, hiddenDim, corpus.classVoc.size(),
                inputDropoutRate, outputDropoutRate)

if not test and not args.random:
    if os.path.exists(wordParamsFile):
        tagger.embedding.load_state_dict(torch.load(wordParamsFile))
    else:
        utils.loadEmbeddings(tagger.embedding, corpus.voc, wordEmbeddingFile)
        torch.save(tagger.embedding.state_dict(), wordParamsFile)

    if os.path.exists(charParamsFile):
        tagger.charEmbedding.load_state_dict(torch.load(charParamsFile))
    else:
        utils.loadEmbeddings(tagger.charEmbedding, corpus.charVoc, charEmbeddingFile)
        torch.save(tagger.charEmbedding.state_dict(), charParamsFile)

if useGpu:
    if torch.cuda.is_available():
        torch.cuda.set_device(args.gpuId)
        torch.cuda.manual_seed(seed)
        tagger.cuda()
        print('**** Running with GPU-' + str(args.gpuId)  + ' ****\n')
    else:
        useGpu = False
        print('**** Warning: GPU is not available ****\n')

criterionTagger = nn.CrossEntropyLoss(size_average = False, ignore_index = -1)

batchListTrain = utils.buildBatchList(len(corpus.trainData), batchSize)
batchListDev = utils.buildBatchList(len(corpus.devData), batchSize)

taggerParams = filter(lambda p: p.requires_grad, tagger.parameters()) # omit p with requires_grad

lstmParams = []
mlpParams = []
withoutWeightDecay = []
for name, param in tagger.named_parameters():
    if not param.requires_grad:
        continue
    if 'bias' in name or 'embedding' in name or 'Embedding' in name:
        withoutWeightDecay += [param]
    elif 'encoder' in name:
        lstmParams += [param]
    else:
        mlpParams += [param]
optParams = [{'params': lstmParams, 'weight_decay': lstmWeightDecay},
             {'params': mlpParams, 'weight_decay': mlpWeightDecay},
             {'params': withoutWeightDecay, 'weight_decay': 0.0}]

opt = optim.SGD(optParams,
                lr = initialLearningRate)

maxDevAcc = -100.0
epoch = 0

while epoch < maxEpoch:
    trainAcc = 0.0
    trainTokenCount = 0.0
    batchProcessed = 0

    for paramGroup in opt.param_groups:
        paramGroup['lr'] = initialLearningRate/(1.0+lrDecay*epoch)

    epoch += 1
    print('--- Epoch '+str(epoch))

    random.shuffle(corpus.trainData)
    tagger.train()
    
    '''
    Mini-batch training
    '''
    for batch in batchListTrain:
        if test:
            break

        opt.zero_grad()
        batchInput, batchChar, batchTarget, lengths, hidden0, tokenCount = corpus.processBatchInfo(batch, True, hiddenDim, useGpu)
        trainTokenCount += tokenCount

        output = tagger(tagger.getBatchedEmbedding(batchInput, batchChar), lengths, hidden0)
        loss = criterionTagger(output, batchTarget)
        loss /= (batch[1]-batch[0]+1.0)
        loss.backward()
        nn.utils.clip_grad_norm(tagger.parameters(), gradClip)
        opt.step()

        _, prediction = torch.max(output, 1)
        trainAcc += (prediction.data == batchTarget.data).sum()

        batchProcessed += 1
        '''
        Mini-batch test
        '''
        if batchProcessed == len(batchListTrain)//20:
            batchProcessed = 0
            devAcc = 0.0
            devTokenCount = 0.0

            tagger.eval()
            for batch in batchListDev:
                batchInput, batchChar, batchTarget, lengths, hidden0, tokenCount = corpus.processBatchInfo(batch, False, hiddenDim, useGpu)
                devTokenCount += tokenCount
        
                output = tagger(tagger.getBatchedEmbedding(batchInput, batchChar), lengths, hidden0)
                _, prediction = torch.max(output, 1)
                devAcc += (prediction.data == batchTarget.data).sum()
            tagger.train()

            devAcc = 100.0*devAcc/devTokenCount
            print('Dev acc.:   '+str(devAcc))

            if devAcc > maxDevAcc:
                maxDevAcc = devAcc
                torch.save(tagger.state_dict(), modelParamsFile)

    if test:
        tagger.load_state_dict(torch.load(modelParamsFile))
        tagger.eval()
        devAcc = 0.0
        devTokenCount = 0.0
        for batch in batchListDev:
            batchInput, batchChar, batchTarget, lengths, hidden0, tokenCount = corpus.processBatchInfo(batch, False, hiddenDim, useGpu)
            devTokenCount += tokenCount

            output = tagger(tagger.getBatchedEmbedding(batchInput, batchChar), lengths, hidden0)
            _, prediction = torch.max(output, 1)

            devAcc += (prediction.data == batchTarget.data).sum()

        devAcc = 100.0*devAcc/devTokenCount
        print('Dev acc.:   '+str(devAcc))

        tagger.train()
        break

    print('Train acc.: '+str(100.0*trainAcc/trainTokenCount))